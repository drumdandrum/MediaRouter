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
import subprocess
import sys
import tarfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
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
SMOKE_BASE_URL = "http://127.0.0.1:18088"
SMOKE_READ_ONLY_ENDPOINTS = (
    "/api/health",
    "/api/system",
    "/api/catalog/summary",
    "/api/integrations/emby/status",
    "/api/broker/status",
    "/api/broker/reservations",
    "/api/outputs/strm/settings",
    "/api/outputs/live-m3u/settings",
)
SMOKE_REPEATED_ENDPOINTS = (
    "/api/catalog/summary",
    "/api/integrations/emby/status",
    "/api/broker/status",
)
SMOKE_SECRET_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]|api[_-]?key\s*[:=]\s*[^\[\s]|"
    r"password\s*[:=]\s*[^\[\s]|token\s*[:=]\s*[^\[\s]|"
    r"https?://[^/\s:@]+:[^@\s/]+@)"
)
TEST_EMBY_API_KEY_VARIABLE = "MEDIA_ROUTER_TEST_EMBY_API_KEY"
MAX_SECRET_FILE_BYTES = 4096
MAX_TEST_EMBY_API_KEY_LENGTH = 1024
SAFE_SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9._~-]+$")


class HarnessError(ValueError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def validate_local_root(local_root: Path, repo_root: Path) -> None:
    expected = repo_root / ".local" / "mac-mini"
    if os.path.abspath(local_root) != os.path.abspath(expected):
        raise HarnessError("refusing to operate on an unexpected local test root")
    for component in (repo_root / ".local", expected):
        if component.is_symlink():
            raise HarnessError("local test root and its parent may not be symlinks")
    if expected.exists() and not expected.is_dir():
        raise HarnessError("local test root must be a directory")
    managed_children = (
        expected / "data",
        expected / "outputs",
        expected / "outputs/movies",
        expected / "outputs/series",
        expected / "outputs/live",
        expected / "logs",
        expected / "snapshots",
        expected / "evidence",
    )
    if any(component.is_symlink() for component in managed_children):
        raise HarnessError("managed local test directories may not be symlinks")


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


def _parse_credential_file(path: Path) -> tuple[str, str | None]:
    """Read and validate once so callers never use different, unvalidated bytes."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing_file", None
    except OSError:
        return "invalid_format", None
    if not stat.S_ISREG(metadata.st_mode):
        return "invalid_format", None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened_metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or stat.S_IMODE(opened_metadata.st_mode) != 0o600
            ):
                state = (
                    "unsafe_mode"
                    if stat.S_ISREG(opened_metadata.st_mode)
                    else "invalid_format"
                )
                return state, None
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(MAX_SECRET_FILE_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError:
        return "invalid_format", None
    if len(raw) > MAX_SECRET_FILE_BYTES or b"\x00" in raw:
        return "invalid_format", None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "invalid_format", None

    value: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            return "invalid_format", None
        name, candidate = line.split("=", 1)
        if name != name.strip() or candidate != candidate.strip():
            return "invalid_format", None
        if name != TEST_EMBY_API_KEY_VARIABLE:
            return "unknown_variable", None
        if value is not None:
            return "duplicate_key", None
        if (
            not candidate
            or len(candidate) > MAX_TEST_EMBY_API_KEY_LENGTH
            or not SAFE_SECRET_VALUE_RE.fullmatch(candidate)
            or "embyserver" in candidate.casefold()
            or "production" in candidate.casefold()
        ):
            return "invalid_format", None
        value = candidate
    return ("valid", value) if value is not None else ("missing_key", None)


def credential_file_state(path: Path) -> str:
    """Validate the local Stage 2 credential file without exposing its value."""
    return _parse_credential_file(path)[0]


def validate_secret_permissions(path: Path) -> None:
    """Keep general harness commands permissive when no Stage 2 file exists."""
    state = credential_file_state(path)
    if state == "missing_file":
        return
    if state != "valid":
        raise HarnessError(f"test credential file is invalid: {state}")


def load_test_emby_api_key(path: Path) -> str:
    """Return the validated key only to an in-process caller; never print it."""
    state, value = _parse_credential_file(path)
    if state != "valid" or value is None:
        raise HarnessError(f"test credential file is invalid: {state}")
    return value


def validate_emby_target(url: str, environment: str, allowed_hosts: str) -> str:
    if environment != "mac-mini-test":
        raise HarnessError("Emby target validation requires environment mac-mini-test")
    parsed = urlsplit(url.strip())
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise HarnessError("test Emby URL must be an explicit credential-free HTTP URL")
    if parsed.port != 8597:
        raise HarnessError("test Emby URL must use the approved port 8597")
    if parsed.path or parsed.query or parsed.fragment:
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
    if path.is_symlink():
        raise HarnessError("feed-id may not be a symlink")
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


def _validate_archive_members(bundle: tarfile.TarFile) -> None:
    for member in bundle.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise HarnessError("snapshot archive contains an unsafe path")
        if member.issym() or member.islnk():
            raise HarnessError("snapshot archive may not contain links")
        if not (member.isfile() or member.isdir()):
            raise HarnessError("snapshot archive may contain only files and directories")
        if not path.parts or path.parts[0] not in {"data", "outputs"}:
            raise HarnessError("snapshot archive contains an unexpected top-level path")


def validate_archive(archive: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        _validate_archive_members(bundle)


def create_archive(source: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "w:gz") as bundle:
            for name in ("data", "outputs"):
                item = source / name
                if item.exists():
                    bundle.add(item, arcname=name, recursive=True)
        validate_archive(archive)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise


def restore_archive(archive: Path, local_root: Path) -> None:
    staging = local_root.parent / f".{local_root.name}-restore"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            # Validate and extract from the same open archive to avoid a
            # replacement race between separate validation and extraction opens.
            _validate_archive_members(bundle)
            # The member validator rejects traversal, links, and special files.
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
    validate_local_root(local_root, repo_root)
    for name in ("data", "outputs", "logs", "evidence"):
        target = local_root / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)


def safe_destroy(local_root: Path, repo_root: Path) -> None:
    validate_local_root(local_root, repo_root)
    if local_root.exists():
        shutil.rmtree(local_root)


def _smoke_compose_command(repo_root: Path, *args: str) -> list[str]:
    return [
        "docker", "compose", "-p", PROJECT,
        "-f", str(repo_root / "docker-compose.yml"),
        "-f", str(repo_root / "deploy/mac-mini/compose.test.yml"),
        "--env-file", str(repo_root / "deploy/mac-mini/.env.test"),
        *args,
    ]


def _smoke_run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


def _smoke_fetch(path: str) -> tuple[int, str]:
    try:
        with urlopen(f"{SMOKE_BASE_URL}{path}", timeout=10) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise HarnessError(f"{path} unavailable: {exc.reason}") from exc


def _smoke_assert_sanitized(text: str) -> None:
    if "embyserver" in text.lower():
        raise HarnessError("production host reference appeared in smoke output")
    if SMOKE_SECRET_PATTERN.search(text):
        raise HarnessError("credential-shaped content appeared in smoke output")


def _smoke_render_and_validate(repo_root: Path) -> None:
    rendered = _smoke_run(
        _smoke_compose_command(repo_root, "config", "--format", "json")
    ).stdout
    validate_compose(json.loads(rendered), repo_root)


def _smoke_verify_fixture(repo_root: Path) -> None:
    manifest_path = repo_root / "tests/fixtures/mac-mini/expected/fixture-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture = repo_root / "tests/fixtures/mac-mini" / manifest["fixture"]
    if file_sha256(fixture) != manifest["sha256"]:
        raise HarnessError("fixture digest does not match its manifest")


def _smoke_container_identity() -> dict:
    state_result = _smoke_run([
        "docker", "inspect", "mediarouter-mac-test-app",
        "--format", "{{json .State}}",
    ])
    state = json.loads(state_result.stdout)
    container_id = _smoke_run([
        "docker", "inspect", "-f", "{{.Id}}", "mediarouter-mac-test-app",
    ]).stdout.strip()
    return {
        "id": container_id,
        "started_at": state.get("StartedAt"),
        "status": state.get("Status"),
    }


def _smoke_sqlite_checks() -> None:
    code = (
        "import sqlite3;"
        "c=sqlite3.connect('file:/data/media_router.db?mode=ro',uri=True);"
        "print(c.execute('PRAGMA integrity_check').fetchone()[0]);"
        "names={'source_import_runs','source_entries','source_entry_occurrences'};"
        "existing={r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")};"
        "print(sum(c.execute('SELECT COUNT(*) FROM '+n).fetchone()[0] for n in names if n in existing));"
        "c.close()"
    )
    result = _smoke_run([
        "docker", "exec", "mediarouter-mac-test-app", "python", "-c", code,
    ])
    lines = result.stdout.splitlines()
    if lines != ["ok", "0"]:
        raise HarnessError(f"unexpected SQLite/ledger state: {lines}")


def _smoke_descriptor_count() -> int | None:
    result = _smoke_run([
        "docker", "exec", "mediarouter-mac-test-app", "sh", "-c",
        "ls /proc/1/fd 2>/dev/null | wc -l",
    ], check=False)
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def run_smoke(repeat: int) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    _smoke_render_and_validate(repo_root)
    _smoke_verify_fixture(repo_root)
    before = _smoke_container_identity()
    before_fd = _smoke_descriptor_count()
    for endpoint in SMOKE_READ_ONLY_ENDPOINTS:
        status, body = _smoke_fetch(endpoint)
        if status != 200:
            raise HarnessError(f"{endpoint} returned HTTP {status}")
        json.loads(body)
        _smoke_assert_sanitized(body)
    for _ in range(repeat):
        for endpoint in SMOKE_REPEATED_ENDPOINTS:
            status, body = _smoke_fetch(endpoint)
            if status != 200:
                raise HarnessError(f"repeated {endpoint} returned HTTP {status}")
            _smoke_assert_sanitized(body)
    _smoke_sqlite_checks()
    logs = _smoke_run(
        _smoke_compose_command(repo_root, "logs", "--tail", "500", "media-router"),
        check=False,
    )
    combined_logs = f"{logs.stdout}\n{logs.stderr}"
    if "database is locked" in combined_logs.lower():
        raise HarnessError("database is locked appeared in recent test logs")
    _smoke_assert_sanitized(combined_logs)
    after = _smoke_container_identity()
    after_fd = _smoke_descriptor_count()
    if before["id"] != after["id"] or before["started_at"] != after["started_at"]:
        raise HarnessError("test container restarted during smoke checks")
    if before_fd is not None and after_fd is not None and after_fd > before_fd + 2:
        raise HarnessError(
            f"descriptor count grew unexpectedly: {before_fd} -> {after_fd}"
        )
    print(json.dumps({
        "status": "passed",
        "project": PROJECT,
        "read_only": True,
        "endpoints": len(SMOKE_READ_ONLY_ENDPOINTS),
        "repeated_calls": repeat * len(SMOKE_REPEATED_ENDPOINTS),
        "container_id": after["id"],
        "descriptor_count_before": before_fd,
        "descriptor_count_after": after_fd,
    }, indent=2, sort_keys=True))


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    compose = sub.add_parser("validate-compose")
    compose.add_argument("config")
    compose.add_argument("repo")
    secret = sub.add_parser("validate-secret")
    secret.add_argument("path")
    credential = sub.add_parser("credential-status")
    credential.add_argument("path")
    credential.add_argument("local_root")
    credential.add_argument("repo")
    local = sub.add_parser("validate-local-root")
    local.add_argument("local_root")
    local.add_argument("repo")
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
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--repeat", type=int, default=10)
    smoke.add_argument("--allow-import", action="store_true")
    smoke.add_argument("--allow-output-generation", action="store_true")
    smoke.add_argument("--allow-playback", action="store_true")
    args = parser.parse_args()

    if args.command == "validate-compose":
        validate_compose(json.loads(Path(args.config).read_text()), Path(args.repo))
    elif args.command == "validate-secret":
        validate_secret_permissions(Path(args.path))
    elif args.command == "credential-status":
        local_root = Path(args.local_root)
        credential_path = Path(args.path)
        validate_local_root(local_root, Path(args.repo))
        if os.path.abspath(credential_path) != os.path.abspath(
            local_root / "secrets.env"
        ):
            raise HarnessError("refusing to inspect an unexpected credential path")
        state = credential_file_state(credential_path)
        print(state)
        if state != "valid":
            return 1
    elif args.command == "validate-local-root":
        validate_local_root(Path(args.local_root), Path(args.repo))
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
    elif args.command == "smoke":
        if args.allow_import or args.allow_output_generation or args.allow_playback:
            parser.error(
                "mutation flags are reserved but not implemented in this read-only harness"
            )
        if not 1 <= args.repeat <= 50:
            parser.error("--repeat must be between 1 and 50")
        run_smoke(args.repeat)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except (HarnessError, json.JSONDecodeError) as exc:
        print(f"harness failed safely: {exc}", file=sys.stderr)
        raise SystemExit(1)
