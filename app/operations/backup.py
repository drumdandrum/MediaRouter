from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
from typing import Any

from app.db.migrations import CURRENT_SCHEMA_VERSION
from app.main_meta import APP_VERSION


BACKUP_FORMAT_VERSION = 1
MAX_ARCHIVE_CONTENT_BYTES = 16 * 1024 * 1024 * 1024
DATABASE_NAME = "media_router.db"
STATE_FILES = (
    "settings.json",
    "wizard_state.json",
    "jobs.json",
    "outputs_strm_settings.json",
    "outputs_live_m3u_settings.json",
    "emby_integration_settings.json",
    ".playback_ticket_secret",
)
ALLOWED_ARCHIVE_FILES = {f"data/{name}" for name in (DATABASE_NAME, *STATE_FILES)} | {"manifest.json"}


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_checks(path: Path) -> dict[str, Any]:
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        migrations_table = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()[0]
        schema_version = (
            conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
            if migrations_table else 0
        )
    if integrity != "ok":
        raise BackupError(f"SQLite integrity check failed: {integrity}")
    if foreign_keys:
        raise BackupError(f"SQLite foreign-key check failed for {len(foreign_keys)} row(s).")
    return {"integrity_check": "ok", "foreign_key_errors": 0, "schema_version": int(schema_version)}


def _validate_json(path: Path) -> None:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"Persistent JSON state is unreadable: {path.name}") from exc


def create_backup(data_dir: Path, destination: Path) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    destination = destination.resolve()
    if not data_dir.is_dir() or data_dir.is_symlink():
        raise BackupError("Data directory must be an existing non-symlink directory.")
    database = data_dir / DATABASE_NAME
    if not database.is_file() or database.is_symlink():
        raise BackupError("MediaRouter SQLite database is missing or unsafe.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise BackupError("Backup destination already exists; refusing to overwrite it.")

    with tempfile.TemporaryDirectory(prefix=".mediarouter-backup-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        staged_data = stage / "data"
        staged_data.mkdir(mode=0o700)
        staged_db = staged_data / DATABASE_NAME
        source_uri = f"file:{database}?mode=ro"
        try:
            with sqlite3.connect(source_uri, uri=True) as source, sqlite3.connect(staged_db) as target:
                source.backup(target)
        except sqlite3.Error as exc:
            raise BackupError("SQLite online backup failed.") from exc
        db_checks = _database_checks(staged_db)
        if db_checks["schema_version"] > CURRENT_SCHEMA_VERSION:
            raise BackupError("Database schema is newer than this MediaRouter build supports.")

        included = [DATABASE_NAME]
        for name in STATE_FILES:
            source = data_dir / name
            if not source.exists():
                continue
            if not source.is_file() or source.is_symlink():
                raise BackupError(f"Persistent state file is unsafe: {name}")
            if name.endswith(".json"):
                _validate_json(source)
            shutil.copyfile(source, staged_data / name)
            included.append(name)

        files = {
            f"data/{name}": {
                "sha256": _sha256(staged_data / name),
                "size": (staged_data / name).stat().st_size,
            }
            for name in sorted(included)
        }
        manifest = {
            "format": "mediarouter-backup",
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": APP_VERSION,
            "schema_version": db_checks["schema_version"],
            "components": {
                "sqlite_database": True,
                "json_state": sorted(name for name in included if name.endswith(".json")),
                "playback_ticket_secret": ".playback_ticket_secret" in included,
                "generated_outputs": False,
                "logs": False,
                "test_fixtures": False,
            },
            "database_checks": db_checks,
            "files": files,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary_archive = stage / "backup.tar.gz"
        with tarfile.open(temporary_archive, "w:gz") as archive:
            archive.add(stage / "manifest.json", arcname="manifest.json", recursive=False)
            for name in sorted(included):
                archive.add(staged_data / name, arcname=f"data/{name}", recursive=False)
        os.chmod(temporary_archive, 0o600)
        os.replace(temporary_archive, destination)
        os.chmod(destination, 0o600)
    return manifest


def _read_archive(archive_path: Path, extraction_dir: Path | None = None) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not archive_path.is_file() or archive_path.is_symlink():
        raise BackupError("Backup archive must be a regular non-symlink file.")
    if archive_path.stat().st_mode & 0o077:
        raise BackupError("Backup archive permissions must not grant group or other access (expected 0600).")
    payloads: dict[str, bytes] = {}
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise BackupError("Backup archive contains duplicate members.")
            if sum(member.size for member in members) > MAX_ARCHIVE_CONTENT_BYTES:
                raise BackupError("Backup archive expands beyond the supported safety limit.")
            for member in members:
                if not member.isfile() or member.name not in ALLOWED_ARCHIVE_FILES:
                    raise BackupError(f"Backup archive contains an unsafe or unexpected member: {member.name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise BackupError(f"Backup member cannot be read: {member.name}")
                payloads[member.name] = extracted.read()
    except (tarfile.TarError, OSError) as exc:
        raise BackupError("Backup archive is unreadable.") from exc
    if "manifest.json" not in payloads or f"data/{DATABASE_NAME}" not in payloads:
        raise BackupError("Backup archive is missing its manifest or SQLite database.")
    try:
        manifest = json.loads(payloads["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("Backup manifest is invalid JSON.") from exc
    if manifest.get("format") != "mediarouter-backup" or manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise BackupError("Backup format is not supported.")
    if int(manifest.get("schema_version", -1)) > CURRENT_SCHEMA_VERSION:
        raise BackupError("Backup schema is newer than this MediaRouter build supports.")
    declared = manifest.get("files")
    if not isinstance(declared, dict) or set(declared) != set(payloads) - {"manifest.json"}:
        raise BackupError("Backup manifest file inventory does not match the archive.")
    for name, metadata in declared.items():
        if not isinstance(metadata, dict):
            raise BackupError(f"Backup manifest metadata is invalid: {name}")
        digest = hashlib.sha256(payloads[name]).hexdigest()
        if digest != metadata.get("sha256") or len(payloads[name]) != metadata.get("size"):
            raise BackupError(f"Backup hash or size mismatch: {name}")
        if name.endswith(".json"):
            try:
                json.loads(payloads[name].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError(f"Backup contains invalid JSON state: {name}") from exc
    if extraction_dir is not None:
        for name, payload in payloads.items():
            if name == "manifest.json":
                continue
            target = extraction_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    return manifest, payloads


def validate_backup(archive_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=".mediarouter-validate-") as temporary:
        root = Path(temporary)
        manifest, _ = _read_archive(archive_path.resolve(), root)
        checks = _database_checks(root / "data" / DATABASE_NAME)
        if checks["schema_version"] != int(manifest["schema_version"]):
            raise BackupError("Backup manifest schema version does not match its database.")
    return manifest


def restore_backup(archive_path: Path, destination: Path, *, dry_run: bool = False) -> dict[str, Any]:
    manifest = validate_backup(archive_path)
    destination = destination.resolve()
    if dry_run:
        return manifest
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
            raise BackupError("Restore destination must not exist or must be an empty non-symlink directory.")
    elif not destination.parent.is_dir() or destination.parent.is_symlink():
        raise BackupError("Restore destination parent must be an existing non-symlink directory.")
    with tempfile.TemporaryDirectory(prefix=".mediarouter-restore-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        _read_archive(archive_path.resolve(), stage)
        staged_data = stage / "data"
        _database_checks(staged_data / DATABASE_NAME)
        for source in staged_data.iterdir():
            os.chmod(source, 0o600)
        os.chmod(staged_data, 0o700)
        if destination.exists():
            destination.rmdir()
        os.replace(staged_data, destination)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create, validate, inspect, or restore MediaRouter backups.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--data-dir", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    for name in ("validate", "inspect"):
        command = commands.add_parser(name)
        command.add_argument("archive", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "create":
            result = create_backup(args.data_dir, args.destination)
        elif args.command in {"validate", "inspect"}:
            result = validate_backup(args.archive)
        else:
            result = restore_backup(args.archive, args.destination, dry_run=args.dry_run)
    except BackupError as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
