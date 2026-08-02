#!/usr/bin/env python3
"""Safety checks for the managed Mac mini test Emby deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tarfile
import tempfile


PROJECT = "emby-mac-test"
SERVICE = "emby"
CONTAINER = "MacEmbyTester"
HOSTNAME = "d6abf72ac521"
IMAGE = "emby/embyserver@sha256:734a6f03c7c783a9e566b08d09a2b6376f41229ff29f032a7e00302e0be98f8a"
IMAGE_ID = "sha256:734a6f03c7c783a9e566b08d09a2b6376f41229ff29f032a7e00302e0be98f8a"
ORIGINAL_ID = "d6abf72ac521d48fea45eff4a54a7d60286115734e5f081e1211db79e3dad28f"
ORIGINAL_VOLUME = "be434b05f00eb341f06016c078d9b852990188a14ccead1884fdf2157061cc73"
SERVER_ID = "312374cb311f4fa28ba32489efc20e39"
VERSION = "4.9.5.0"
VOLUME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,127}$")
FORBIDDEN = (
    "embyserver:", "//embyserver", "/users/shared/mediarouter", "/opt/mediarouter",
    "secrets.env", "feed-id", "snapshots", "/outputs/live", "\\config", "\\media",
)


class EmbyHarnessError(ValueError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_volume_name(value: str) -> str:
    lowered = value.casefold()
    if (not VOLUME_RE.fullmatch(value) or value == ORIGINAL_VOLUME
            or "production" in lowered or "embyserver" in lowered):
        raise EmbyHarnessError("unsafe or non-stable config volume name")
    return value


def validate_managed_local_root(local_root: Path, repo_root: Path) -> None:
    expected = repo_root / ".local" / "mac-mini" / "emby-test"
    if os.path.abspath(local_root) != os.path.abspath(expected):
        raise EmbyHarnessError("unexpected managed Emby local root")
    for path in (repo_root / ".local", repo_root / ".local" / "mac-mini", expected,
                 expected / "backups", expected / "evidence"):
        if path.is_symlink():
            raise EmbyHarnessError("managed Emby local paths may not be symlinks")
        if path.exists() and not path.is_dir():
            raise EmbyHarnessError("managed Emby local paths must be directories")


def validate_backup_location(path: Path, backup_root: Path) -> None:
    if backup_root.is_symlink() or not backup_root.is_dir():
        raise EmbyHarnessError("backup root must be a regular directory")
    if path.is_symlink() or os.path.abspath(path.parent) != os.path.abspath(backup_root):
        raise EmbyHarnessError("backup must remain directly beneath the protected backup root")
    if not path.name.startswith("config-") or not path.name.endswith(".tar.gz"):
        raise EmbyHarnessError("unexpected backup archive name")


def validate_compose(config: dict, repo_root: Path, volume_name: str) -> None:
    validate_volume_name(volume_name)
    if config.get("name") != PROJECT:
        raise EmbyHarnessError("unexpected Compose project")
    services = config.get("services") or {}
    if set(services) != {SERVICE}:
        raise EmbyHarnessError("managed Emby project must contain exactly one service")
    service = services[SERVICE]
    if service.get("container_name") != CONTAINER or service.get("hostname") != HOSTNAME:
        raise EmbyHarnessError("unexpected container name or hostname")
    if service.get("image") != IMAGE:
        raise EmbyHarnessError("Emby image must be pinned to the approved digest")
    if service.get("platform") != "linux/arm64":
        raise EmbyHarnessError("managed Emby platform must be linux/arm64")
    if service.get("stop_grace_period") != "1m0s":
        raise EmbyHarnessError("managed Emby stop grace period must be 60 seconds")
    if service.get("security_opt") != ["no-new-privileges:true"]:
        raise EmbyHarnessError("no-new-privileges must be enabled exactly")
    if (service.get("privileged", False) or service.get("devices") or service.get("cap_add")
            or service.get("cap_drop") or service.get("extra_hosts") or service.get("network_mode")):
        raise EmbyHarnessError("privileges, devices, and added capabilities are forbidden")
    if set(service.get("networks") or {}) != {"default"} or set(config.get("networks") or {}) != {"default"}:
        raise EmbyHarnessError("only the project-default network is permitted")
    if service.get("restart") not in (None, "no", "none"):
        raise EmbyHarnessError("restart policy must be disabled")
    rendered = json.dumps(config, sort_keys=True).casefold()
    for value in FORBIDDEN:
        if value.casefold() in rendered:
            raise EmbyHarnessError(f"forbidden path or production reference: {value}")
    ports = service.get("ports") or []
    if len(ports) != 1:
        raise EmbyHarnessError("exactly one port is required")
    port = ports[0]
    if (port.get("host_ip") != "127.0.0.1" or int(port.get("published", 0)) != 8597
            or int(port.get("target", 0)) != 8096):
        raise EmbyHarnessError("Emby must bind only 127.0.0.1:8597:8096")
    mounts = service.get("volumes") or []
    if len(mounts) != 3:
        raise EmbyHarnessError("exactly three mounts are required")
    by_target = {row.get("target"): row for row in mounts}
    expected = {"/config", "/media-router-test/movies", "/media-router-test/series"}
    if set(by_target) != expected:
        raise EmbyHarnessError("unexpected mount target")
    config_mount = by_target["/config"]
    logical_volume = config_mount.get("source")
    declared_volume = (config.get("volumes") or {}).get(logical_volume, {})
    if (config_mount.get("type") != "volume" or not logical_volume
            or declared_volume.get("name") != volume_name
            or not declared_volume.get("external", False)):
        raise EmbyHarnessError("/config must use the approved stable named volume")
    local_root = repo_root / ".local" / "mac-mini"
    for kind in ("movies", "series"):
        row = by_target[f"/media-router-test/{kind}"]
        expected_source = local_root / "outputs" / kind
        source = Path(row.get("source", ""))
        if (row.get("type") != "bind" or not row.get("read_only", False)
                or source.resolve() != expected_source.resolve()
                or not _within(source, local_root)):
            raise EmbyHarnessError(f"{kind} must use the approved read-only output root")


def validate_original_inspect(
    data: dict, expected_id: str = ORIGINAL_ID, expected_name: str = CONTAINER
) -> None:
    if data.get("Id") != expected_id or data.get("Name") != f"/{expected_name}":
        raise EmbyHarnessError("original container identity mismatch")
    if data.get("Image") != IMAGE_ID:
        raise EmbyHarnessError("original image identity mismatch")
    mounts = data.get("Mounts") or []
    configs = [m for m in mounts if m.get("Destination") == "/config"]
    if len(configs) != 1 or configs[0].get("Name") != ORIGINAL_VOLUME:
        raise EmbyHarnessError("authoritative original config volume mismatch")


def _docker_desktop_host_path(value: str) -> Path:
    if value.startswith("/host_mnt/"):
        value = value[len("/host_mnt"):]
    return Path(value)


def validate_replacement_inspect(data: dict, volume_name: str, repo_root: Path) -> None:
    validate_volume_name(volume_name)
    if data.get("Name") != f"/{CONTAINER}" or data.get("Image") != IMAGE_ID:
        raise EmbyHarnessError("replacement identity mismatch")
    if (data.get("Config") or {}).get("Hostname") != HOSTNAME:
        raise EmbyHarnessError("replacement hostname mismatch")
    host = data.get("HostConfig") or {}
    if (host.get("Privileged") or host.get("Devices") or host.get("CapAdd")
            or host.get("CapDrop") or host.get("ExtraHosts")
            or host.get("SecurityOpt") != ["no-new-privileges:true"]):
        raise EmbyHarnessError("replacement runtime privileges mismatch")
    ports = ((data.get("NetworkSettings") or {}).get("Ports") or {}).get("8096/tcp") or []
    if ports != [{"HostIp": "127.0.0.1", "HostPort": "8597"}]:
        raise EmbyHarnessError("replacement port is not loopback-only")
    mounts = data.get("Mounts") or []
    by_target = {m.get("Destination"): m for m in mounts}
    if set(by_target) != {"/config", "/media-router-test/movies", "/media-router-test/series"}:
        raise EmbyHarnessError("replacement has unexpected mounts")
    if by_target["/config"].get("Name") != volume_name or not by_target["/config"].get("RW"):
        raise EmbyHarnessError("replacement config volume mismatch")
    for kind in ("movies", "series"):
        target = f"/media-router-test/{kind}"
        source = _docker_desktop_host_path(by_target[target].get("Source", ""))
        expected = repo_root / ".local" / "mac-mini" / "outputs" / kind
        if by_target[target].get("RW") or source.resolve() != expected.resolve():
            raise EmbyHarnessError("replacement media mounts must be read-only")


def validate_backup_archive(path: Path, *, require_mode: bool = True) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise EmbyHarnessError("backup must be a regular non-symlink file")
    if require_mode and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise EmbyHarnessError("backup archive mode must be 0600")
    count = total = 0
    database_members: list[str] = []
    with tarfile.open(path, "r:gz") as bundle:
        seen: set[str] = set()
        for member in bundle.getmembers():
            pure = PurePosixPath(member.name)
            normalized = pure.as_posix().lstrip("./")
            if not normalized and member.isdir() and member.name in {".", "./"}:
                continue
            if pure.is_absolute() or ".." in pure.parts or not normalized:
                raise EmbyHarnessError("backup contains an unsafe path")
            folded = normalized.casefold()
            if folded in seen:
                raise EmbyHarnessError("backup contains duplicate paths")
            seen.add(folded)
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise EmbyHarnessError("backup contains a link or special member")
            if member.isfile():
                count += 1
                total += member.size
                if normalized.startswith("data/") and normalized.endswith(".db"):
                    database_members.append(member.name)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"file_count": count, "total_bytes": total, "archive_bytes": path.stat().st_size,
            "archive_sha256": digest.hexdigest(), "database_count": len(database_members)}


def sqlite_checks_from_backup(path: Path) -> dict[str, str]:
    validate_backup_archive(path)
    results: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="emby-backup-check-") as temp:
        root = Path(temp)
        with tarfile.open(path, "r:gz") as bundle:
            members = []
            for member in bundle.getmembers():
                normalized = PurePosixPath(member.name).as_posix().lstrip("./")
                if normalized.startswith("data/") and (
                    normalized.endswith(".db") or normalized.endswith(".db-wal")
                    or normalized.endswith(".db-shm")
                ):
                    members.append(member)
            bundle.extractall(root, members=members)
        for db in sorted(root.rglob("*.db")):
            try:
                with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                    value = conn.execute("PRAGMA integrity_check").fetchone()[0]
                results[db.name] = value
            except sqlite3.Error as exc:
                raise EmbyHarnessError(f"offline SQLite integrity unavailable for {db.name}") from exc
    if not results or any(value != "ok" for value in results.values()):
        raise EmbyHarnessError("offline SQLite integrity check failed")
    return results


def require_available_rollback_name(existing: set[str], candidate: str) -> str:
    if not re.fullmatch(r"MacEmbyTester-rollback-[0-9]{8}-[0-9]{6}", candidate):
        raise EmbyHarnessError("invalid rollback container name")
    if candidate in existing:
        raise EmbyHarnessError("rollback container name already exists")
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    compose = sub.add_parser("validate-compose")
    compose.add_argument("path"); compose.add_argument("repo"); compose.add_argument("volume")
    original = sub.add_parser("validate-original-inspect")
    original.add_argument("path"); original.add_argument("expected_id", nargs="?", default=ORIGINAL_ID)
    original.add_argument("expected_name", nargs="?", default=CONTAINER)
    replacement = sub.add_parser("validate-replacement-inspect")
    replacement.add_argument("path"); replacement.add_argument("volume"); replacement.add_argument("repo")
    backup = sub.add_parser("validate-backup")
    backup.add_argument("path"); backup.add_argument("--sqlite", action="store_true")
    volume = sub.add_parser("validate-volume")
    volume.add_argument("name")
    local = sub.add_parser("validate-local-root")
    local.add_argument("path"); local.add_argument("repo")
    location = sub.add_parser("validate-backup-location")
    location.add_argument("path"); location.add_argument("root")
    args = parser.parse_args()
    if args.command == "validate-compose":
        validate_compose(json.loads(Path(args.path).read_text()), Path(args.repo), args.volume)
    elif args.command == "validate-original-inspect":
        validate_original_inspect(
            json.loads(Path(args.path).read_text()), args.expected_id, args.expected_name
        )
    elif args.command == "validate-replacement-inspect":
        validate_replacement_inspect(json.loads(Path(args.path).read_text()), args.volume, Path(args.repo))
    elif args.command == "validate-backup":
        result = validate_backup_archive(Path(args.path))
        if args.sqlite:
            result["sqlite_integrity"] = sqlite_checks_from_backup(Path(args.path))
        print(json.dumps(result, sort_keys=True))
    elif args.command == "validate-volume":
        print(validate_volume_name(args.name))
    elif args.command == "validate-local-root":
        validate_managed_local_root(Path(args.path), Path(args.repo))
    elif args.command == "validate-backup-location":
        validate_backup_location(Path(args.path), Path(args.root))


if __name__ == "__main__":
    main()
