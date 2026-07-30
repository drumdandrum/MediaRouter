#!/usr/bin/env python3
"""Safety and archive helpers for the isolated Mac mini test harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tarfile
from urllib.parse import urlsplit
from uuid import UUID


PROJECT = "mediarouter-mac-test"
CONTAINER = "mediarouter-mac-test-app"
FORBIDDEN_TEXT = (
    "embyserver",
    "/opt/mediarouter",
    "/users/shared/iptvboss",
    "/users/shared/mediarouter",
    "cloudflare",
    "cloudflared",
    "nginx",
    "tunnel",
)
EXPECTED_TARGETS = {
    "/data": ".local/mac-mini/data",
    "/outputs/movies": ".local/mac-mini/outputs/movies",
    "/outputs/series": ".local/mac-mini/outputs/series",
    "/outputs/live": ".local/mac-mini/outputs/live",
}
FIXTURE_TARGETS = {"/fixtures", "/iptvboss/outputs"}


class HarnessError(ValueError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def validate_compose(config: dict, repo_root: Path) -> None:
    if config.get("name") != PROJECT:
        raise HarnessError(f"unsafe Compose project: {config.get('name')!r}")
    services = config.get("services", {})
    if set(services) != {"media-router"}:
        raise HarnessError("the test project must contain only the media-router service")
    service = services["media-router"]
    if service.get("container_name") != CONTAINER:
        raise HarnessError("unexpected test container name")
    rendered = json.dumps(config, sort_keys=True).lower()
    for forbidden in FORBIDDEN_TEXT:
        if forbidden in rendered:
            raise HarnessError(f"forbidden production or proxy reference: {forbidden}")

    ports = service.get("ports", [])
    if len(ports) != 1:
        raise HarnessError("exactly one API port binding is required")
    port = ports[0]
    if (
        port.get("host_ip") != "127.0.0.1"
        or int(port.get("target", 0)) != 8088
        or int(port.get("published", 0)) != 18088
    ):
        raise HarnessError("API must bind only 127.0.0.1:18088:8088")

    environment = service.get("environment", {})
    if str(environment.get("MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED", "")).lower() != "false":
        raise HarnessError("source-entry shadow ledger must be explicitly false")
    if environment.get("MEDIA_ROUTER_PUBLIC_BASE_URL") != "http://host.docker.internal:18088":
        raise HarnessError("unexpected public/runtime URL")

    mounts = service.get("volumes", [])
    by_target = {item.get("target"): item for item in mounts}
    if set(by_target) != set(EXPECTED_TARGETS) | FIXTURE_TARGETS:
        raise HarnessError("unexpected or missing volume target")
    isolated_root = repo_root / ".local" / "mac-mini"
    for target, suffix in EXPECTED_TARGETS.items():
        source = Path(by_target[target]["source"])
        expected = repo_root / suffix
        if source.resolve() != expected.resolve() or not _within(source, isolated_root):
            raise HarnessError(f"{target} is not isolated beneath .local/mac-mini")
    fixtures = (repo_root / "tests" / "fixtures" / "mac-mini").resolve()
    for target in FIXTURE_TARGETS:
        mount = by_target[target]
        if Path(mount["source"]).resolve() != fixtures or not mount.get("read_only", False):
            raise HarnessError(f"{target} must be the read-only committed fixture mount")
    if service.get("restart") not in ("no", "none", None):
        raise HarnessError("restart policy must be disabled")
    if not service.get("healthcheck"):
        raise HarnessError("container healthcheck is required")


def validate_secret_permissions(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise HarnessError(f"{path} must be a regular file with mode 0600")


def validate_emby_target(url: str, environment: str, allowed_hosts: str) -> str:
    if environment != "mac-mini-test":
        raise HarnessError("Emby target validation requires environment mac-mini-test")
    parsed = urlsplit(url.strip())
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise HarnessError("test Emby URL must be an explicit credential-free HTTP URL")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise HarnessError("test Emby URL must not contain a path, query, or fragment")
    authority = parsed.netloc.lower()
    if "embyserver" in authority:
        raise HarnessError("production Emby targets are forbidden")
    allowed = {item.strip().lower() for item in allowed_hosts.split(",") if item.strip()}
    if authority not in allowed:
        raise HarnessError("Emby target is not in the local test allowlist")
    if parsed.hostname not in {"host.docker.internal", "localhost", "127.0.0.1"}:
        raise HarnessError("public or nonlocal Emby hosts are forbidden")
    return f"http://{authority}"


def ensure_feed_id(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        parsed = UUID(value)
    else:
        from uuid import uuid4

        parsed = uuid4()
        path.write_text(f"{parsed}\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return str(parsed)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sqlite_integrity(path: Path) -> str:
    if not path.exists():
        return "not_present"
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
    if rows != ["ok"]:
        raise HarnessError(f"SQLite integrity check failed: {rows[:3]}")
    return "ok"


def validate_archive(archive: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise HarnessError("snapshot archive contains an unsafe path")
            if member.issym() or member.islnk():
                raise HarnessError("snapshot archive may not contain links")


def create_archive(source: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("data", "outputs"):
            item = source / name
            if item.exists():
                bundle.add(item, arcname=name, recursive=True)


def restore_archive(archive: Path, local_root: Path) -> None:
    validate_archive(archive)
    staging = local_root.parent / f".{local_root.name}-restore"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            # validate_archive rejects absolute/traversal paths and all links first.
            # Avoid the newer tarfile filter argument so the operator helper remains
            # usable with the Python versions commonly installed on macOS.
            bundle.extractall(staging)
        for name in ("data", "outputs"):
            destination = local_root / name
            if destination.exists():
                shutil.rmtree(destination)
            source = staging / name
            if source.exists():
                shutil.move(str(source), destination)
            else:
                destination.mkdir(parents=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def safe_reset(local_root: Path, repo_root: Path) -> None:
    expected = repo_root / ".local" / "mac-mini"
    if local_root.resolve() != expected.resolve():
        raise HarnessError("refusing to reset an unexpected directory")
    for name in ("data", "outputs", "logs", "evidence"):
        target = local_root / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)


def safe_destroy(local_root: Path, repo_root: Path) -> None:
    expected = repo_root / ".local" / "mac-mini"
    if local_root.resolve() != expected.resolve():
        raise HarnessError("refusing to destroy an unexpected directory")
    if local_root.exists():
        shutil.rmtree(local_root)


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    compose = sub.add_parser("validate-compose")
    compose.add_argument("config")
    compose.add_argument("repo")
    secret = sub.add_parser("validate-secret")
    secret.add_argument("path")
    target = sub.add_parser("validate-emby-target")
    target.add_argument("url")
    target.add_argument("environment")
    target.add_argument("allowed_hosts")
    feed = sub.add_parser("feed-id")
    feed.add_argument("path")
    integrity = sub.add_parser("integrity")
    integrity.add_argument("path")
    archive = sub.add_parser("validate-archive")
    archive.add_argument("path")
    create = sub.add_parser("create-archive")
    create.add_argument("source")
    create.add_argument("archive")
    restore = sub.add_parser("restore-archive")
    restore.add_argument("archive")
    restore.add_argument("local_root")
    reset = sub.add_parser("reset")
    reset.add_argument("local_root")
    reset.add_argument("repo")
    destroy = sub.add_parser("destroy")
    destroy.add_argument("local_root")
    destroy.add_argument("repo")
    digest = sub.add_parser("sha256")
    digest.add_argument("path")
    args = parser.parse_args()

    if args.command == "validate-compose":
        validate_compose(json.loads(Path(args.config).read_text()), Path(args.repo))
    elif args.command == "validate-secret":
        validate_secret_permissions(Path(args.path))
    elif args.command == "validate-emby-target":
        print(validate_emby_target(args.url, args.environment, args.allowed_hosts))
    elif args.command == "feed-id":
        print(ensure_feed_id(Path(args.path)))
    elif args.command == "integrity":
        print(sqlite_integrity(Path(args.path)))
    elif args.command == "validate-archive":
        validate_archive(Path(args.path))
    elif args.command == "create-archive":
        create_archive(Path(args.source), Path(args.archive))
    elif args.command == "restore-archive":
        restore_archive(Path(args.archive), Path(args.local_root))
    elif args.command == "reset":
        safe_reset(Path(args.local_root), Path(args.repo))
    elif args.command == "destroy":
        safe_destroy(Path(args.local_root), Path(args.repo))
    elif args.command == "sha256":
        print(file_sha256(Path(args.path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
