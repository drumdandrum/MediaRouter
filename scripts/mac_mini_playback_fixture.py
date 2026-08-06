#!/usr/bin/env python3
"""Guarded helpers for the isolated Mac mini playable-media fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROJECT = "mediarouter-mac-playback-fixture"
SERVICE = "playback-fixture"
CONTAINER = "mediarouter-mac-playback-fixture"
IMAGE = (
    "nginxinc/nginx-unprivileged:1.27.5-alpine@"
    "sha256:025de0b541bcc6cfbf508e4201aaa37b51d34daaf6d52e5e2753e29a8ddaa869"
)
HOST_URL = "http://127.0.0.1:18091/playback-test.mp4"
CONTAINER_URL = "http://host.docker.internal:18091/playback-test.mp4"
MEDIA_NAME = "playback-test.mp4"
LOCAL_MANIFEST_NAME = "fixture-state.json"
EXPECTED_DURATION = 20.0
EXPECTED_WIDTH = 1280
EXPECTED_HEIGHT = 720
EXPECTED_FRAME_RATE = "30/1"


class FixtureError(ValueError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def fixture_paths(repo_root: Path) -> dict[str, Path]:
    repo = repo_root.resolve()
    root = repo / ".local" / "mac-mini" / "playback-fixture"
    return {
        "root": root,
        "media": root / "media",
        "file": root / "media" / MEDIA_NAME,
        "manifest": root / LOCAL_MANIFEST_NAME,
        "nginx": repo / "deploy" / "mac-mini" / "playback-fixture.nginx.conf",
    }


def validate_fixture_root(repo_root: Path, *, create: bool = False) -> dict[str, Path]:
    paths = fixture_paths(repo_root)
    expected_parent = repo_root.resolve() / ".local" / "mac-mini"
    for component in (repo_root / ".local", expected_parent, paths["root"], paths["media"]):
        if component.is_symlink():
            raise FixtureError("fixture path components may not be symlinks")
        if component.exists() and not component.is_dir():
            raise FixtureError("fixture path components must be directories")
    if not _within(paths["root"], expected_parent):
        raise FixtureError("fixture root must remain beneath .local/mac-mini")
    if create:
        paths["root"].mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(paths["root"], 0o700)
        paths["media"].mkdir(mode=0o755, exist_ok=True)
        os.chmod(paths["media"], 0o755)
    elif not paths["media"].is_dir():
        raise FixtureError("generate the playable fixture first")
    for key in ("file", "manifest"):
        path = paths[key]
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise FixtureError("fixture files must be regular non-symlink files")
    return paths


def generation_command(output: Path, *, overwrite: bool = False) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=20",
        "-filter:a", "volume=0.05", "-c:v", "libx264", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "3.1",
        "-r", "30", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-metadata", "title=MediaRouter Playback Test",
        "-metadata", "comment=synthetic lab-only fixture", "-y" if overwrite else "-n",
        str(output),
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _probe_media(path: Path) -> dict[str, object]:
    if shutil.which("ffprobe") is None:
        raise FixtureError("ffprobe is required to validate the synthetic fixture")
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,format_name:stream=codec_type,codec_name,width,height,r_frame_rate",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration", 0))
    if not video or video.get("codec_name") != "h264":
        raise FixtureError("fixture video must be H.264")
    if video.get("width") != EXPECTED_WIDTH or video.get("height") != EXPECTED_HEIGHT:
        raise FixtureError("fixture dimensions differ from 1280x720")
    if video.get("r_frame_rate") != EXPECTED_FRAME_RATE:
        raise FixtureError("fixture frame rate differs from 30 fps")
    if not audio or audio.get("codec_name") != "aac":
        raise FixtureError("fixture audio must be AAC")
    if abs(duration - EXPECTED_DURATION) > 0.1:
        raise FixtureError("fixture duration differs from 20 seconds")
    return {
        "duration_seconds": duration,
        "format_name": data.get("format", {}).get("format_name"),
        "video": {key: video.get(key) for key in ("codec_name", "width", "height", "r_frame_rate")},
        "audio": {"codec_name": audio.get("codec_name")},
    }


def generate_fixture(repo_root: Path, *, overwrite: bool = False) -> dict[str, object]:
    paths = validate_fixture_root(repo_root, create=True)
    if paths["file"].exists() and not overwrite:
        raise FixtureError("fixture media already exists; use --overwrite explicitly")
    if paths["manifest"].exists() and not overwrite:
        raise FixtureError("fixture state already exists; use --overwrite explicitly")
    if shutil.which("ffmpeg") is None:
        raise FixtureError("ffmpeg is required to generate the synthetic fixture")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".playback-test.", suffix=".mp4", dir=paths["media"]
    )
    os.close(descriptor)
    temporary_file = Path(temporary_name)
    temporary_file.unlink()
    temporary_manifest = paths["root"] / f".{LOCAL_MANIFEST_NAME}.{os.getpid()}.tmp"
    command = generation_command(temporary_file, overwrite=False)
    try:
        subprocess.run(command, check=True)
        os.chmod(temporary_file, 0o644)
        media = _probe_media(temporary_file)
        result = {
            "lab_only": True,
            "path": str(paths["file"].relative_to(repo_root.resolve())),
            "sha256": _sha256(temporary_file),
            "size_bytes": temporary_file.stat().st_size,
            "generation_command": generation_command(
                Path(".local/mac-mini/playback-fixture/media") / MEDIA_NAME,
                overwrite=overwrite,
            ),
            **media,
        }
        descriptor = os.open(temporary_manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_file, paths["file"])
        os.replace(temporary_manifest, paths["manifest"])
        return result
    except BaseException:
        temporary_file.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)
        raise


def validate_committed_fixture(repo_root: Path) -> dict[str, object]:
    fixture_root = repo_root.resolve() / "tests" / "fixtures" / "mac-mini" / "playback"
    playlist = fixture_root / "playback-one.m3u"
    manifest_path = fixture_root / "fixture-manifest.json"
    if playlist.is_symlink() or manifest_path.is_symlink():
        raise FixtureError("committed playback fixture files may not be symlinks")
    lines = [line.strip() for line in playlist.read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = [line for line in lines if line.startswith("#EXTINF:")]
    locators = [line for line in lines if not line.startswith("#")]
    if len(entries) != 1 or locators != [CONTAINER_URL]:
        raise FixtureError("playback playlist must contain exactly one approved locator")
    extinf = entries[0]
    for expected in (
        'CUID="lab-playback-movie-001"',
        'tvg-id="lab-playback-movie-001"',
        'tvg-name="MediaRouter Playback Test"',
        ",MediaRouter Playback Test",
    ):
        if expected not in extinf:
            raise FixtureError("playback playlist identity differs from the manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_manifest = {
        "title": "MediaRouter Playback Test",
        "cuid": "lab-playback-movie-001",
        "tvg_id": "lab-playback-movie-001",
        "media_type": "movie",
        "expected_locator_authority": "host.docker.internal:18091",
        "expected_generated_media_relative_path": ".local/mac-mini/playback-fixture/media/playback-test.mp4",
        "lab_only": True,
    }
    for key, value in expected_manifest.items():
        if manifest.get(key) != value:
            raise FixtureError(f"unexpected committed playback manifest field: {key}")
    rendered = playlist.read_text(encoding="utf-8").casefold() + json.dumps(manifest).casefold()
    for forbidden in ("embyserver", "production", "media.invalid", "api_key", "password", "token"):
        if forbidden in rendered:
            raise FixtureError("playback fixture contains a forbidden reference")
    return manifest


def validate_generated_fixture(repo_root: Path) -> dict[str, object]:
    paths = validate_fixture_root(repo_root)
    if not paths["file"].is_file() or not paths["manifest"].is_file():
        raise FixtureError("generated fixture media and local state are required")
    if stat.S_IMODE(paths["manifest"].stat().st_mode) != 0o600:
        raise FixtureError("fixture state must use mode 0600")
    state = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    if state.get("sha256") != _sha256(paths["file"]):
        raise FixtureError("generated fixture digest differs from local state")
    current = _probe_media(paths["file"])
    for key in ("duration_seconds", "format_name", "video", "audio"):
        if current[key] != state.get(key):
            raise FixtureError("generated fixture codec metadata differs from local state")
    return state


def validate_compose(config: dict, repo_root: Path) -> None:
    if config.get("name") != PROJECT or set(config.get("services", {})) != {SERVICE}:
        raise FixtureError("unexpected playback-fixture Compose project or service")
    service = config["services"][SERVICE]
    if service.get("container_name") != CONTAINER or service.get("image") != IMAGE:
        raise FixtureError("unexpected playback-fixture container or image")
    if service.get("platform") != "linux/arm64":
        raise FixtureError("playback fixture must use linux/arm64")
    ports = service.get("ports", [])
    if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1" or int(ports[0].get("published", 0)) != 18091 or int(ports[0].get("target", 0)) != 8080:
        raise FixtureError("playback fixture must bind only 127.0.0.1:18091:8080")
    mounts = {row.get("target"): row for row in service.get("volumes", [])}
    if set(mounts) != {"/srv/media", "/etc/nginx/nginx.conf"}:
        raise FixtureError("unexpected playback-fixture mount")
    expected = fixture_paths(repo_root)
    if Path(mounts["/srv/media"]["source"]).resolve() != expected["media"].resolve() or not mounts["/srv/media"].get("read_only"):
        raise FixtureError("fixture media mount must be exact and read-only")
    if Path(mounts["/etc/nginx/nginx.conf"]["source"]).resolve() != expected["nginx"].resolve() or not mounts["/etc/nginx/nginx.conf"].get("read_only"):
        raise FixtureError("fixture nginx mount must be exact and read-only")
    rendered = json.dumps(config, sort_keys=True).casefold()
    for forbidden in ("embyserver", "/users/shared/mediarouter", "/opt/mediarouter", "secrets.env", "feed-id", "snapshots", "backups", "/config", "/data", "/outputs"):
        if forbidden in rendered:
            raise FixtureError("forbidden production or private mount/reference")
    if not service.get("read_only") or service.get("privileged"):
        raise FixtureError("fixture container root must be read-only and unprivileged")
    if set(service.get("cap_drop", [])) != {"ALL"}:
        raise FixtureError("fixture container must drop all capabilities")
    if "no-new-privileges:true" not in service.get("security_opt", []):
        raise FixtureError("fixture container must enable no-new-privileges")
    if service.get("tmpfs") != ["/tmp:size=16m,mode=1777"]:
        raise FixtureError("fixture container must use only the approved /tmp tmpfs")
    if service.get("restart") not in ("no", "none", None) or not service.get("healthcheck"):
        raise FixtureError("fixture restart and healthcheck policy is unsafe")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FixtureError("fixture probe refuses redirects")


def probe_fixture(url: str = HOST_URL) -> dict[str, object]:
    if url != HOST_URL:
        raise FixtureError("fixture probe URL must be the loopback-only approved URL")
    opener = build_opener(_NoRedirect)
    try:
        with opener.open(Request(url, method="HEAD"), timeout=5) as response:
            head = {key.casefold(): value for key, value in response.headers.items()}
            total = int(head["content-length"])
        with opener.open(Request(url, headers={"Range": "bytes=0-1023"}), timeout=5) as response:
            body = response.read(1025)
            ranged = {key.casefold(): value for key, value in response.headers.items()}
            status = response.status
    except (HTTPError, URLError, KeyError, ValueError) as exc:
        raise FixtureError("fixture HTTP probe failed") from exc
    if status != 206 or len(body) != 1024:
        raise FixtureError("fixture server did not honor the byte range")
    if ranged.get("content-range") != f"bytes 0-1023/{total}":
        raise FixtureError("fixture Content-Range is invalid")
    if ranged.get("content-type", "").split(";", 1)[0] != "video/mp4":
        raise FixtureError("fixture Content-Type must be video/mp4")
    for validator in ("etag", "last-modified"):
        if head.get(validator) and ranged.get(validator) != head.get(validator):
            raise FixtureError("fixture validators changed between HEAD and range request")
    return {"status": "passed", "url": url, "content_length": total, "range_status": status, "content_type": ranged.get("content-type"), "etag": ranged.get("etag"), "last_modified": ranged.get("last-modified")}


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    root = sub.add_parser("validate-root")
    root.add_argument("repo")
    generate = sub.add_parser("generate")
    generate.add_argument("repo")
    generate.add_argument("--overwrite", action="store_true")
    media = sub.add_parser("validate-media")
    media.add_argument("repo")
    compose = sub.add_parser("validate-compose")
    compose.add_argument("config")
    compose.add_argument("repo")
    probe = sub.add_parser("probe")
    probe.add_argument("--url", default=HOST_URL)
    committed = sub.add_parser("validate-committed")
    committed.add_argument("repo")
    args = parser.parse_args()
    if args.command == "validate-root":
        validate_fixture_root(Path(args.repo), create=False)
    elif args.command == "generate":
        print(json.dumps(generate_fixture(Path(args.repo), overwrite=args.overwrite), indent=2, sort_keys=True))
    elif args.command == "validate-media":
        print(json.dumps(validate_generated_fixture(Path(args.repo)), indent=2, sort_keys=True))
    elif args.command == "validate-compose":
        validate_compose(json.loads(Path(args.config).read_text(encoding="utf-8")), Path(args.repo))
    elif args.command == "probe":
        print(json.dumps(probe_fixture(args.url), indent=2, sort_keys=True))
    elif args.command == "validate-committed":
        print(json.dumps(validate_committed_fixture(Path(args.repo)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except (FixtureError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"playback fixture failed safely: {exc}", file=sys.stderr)
        raise SystemExit(1)
