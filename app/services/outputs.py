from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time

from app.core.config import get_settings
from app.core.json_store import JsonStore
from app.schemas.catalog import CatalogItem
from app.schemas.outputs import (
    GeneratedOutputFile,
    LiveM3uOutputResult,
    LiveM3uOutputSummary,
    LiveM3uPreviewEntry,
    LiveM3uRunHistory,
    LiveM3uSettings,
    LiveM3uSettingsUpdate,
    OutputPathValidation,
    OutputPathValidationResult,
    OutputRunHistory,
    StrmOutputOperation,
    StrmOutputResult,
    StrmOutputSummary,
    StrmSettings,
    StrmSettingsUpdate,
)
from app.services.catalog import ensure_schema, list_items
from app.services.jobs import update_job
from app.services.logs import add_log
from app.services.runtime import public_runtime_base_url, route_for_media_type
from app.services.settings import get_app_settings


OUTPUT_TYPE = "strm"
OUTPUT_ID = "strm"
LIVE_M3U_OUTPUT_TYPE = "live-m3u"
LIVE_M3U_OUTPUT_ID = "live-m3u"
MAX_OUTPUT_ROWS = 500
UVICORN_LOGGER = logging.getLogger("uvicorn.error")


class OutputPathError(Exception):
    pass


def _db_path() -> Path:
    return get_settings().data_dir / "media_router.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_schema(conn)
    ensure_outputs_schema(conn)
    return conn


def ensure_outputs_schema(conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    if conn is None:
        conn = sqlite3.connect(_db_path())
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS output_generated_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            output_id TEXT NOT NULL,
            catalog_item_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            output_type TEXT NOT NULL,
            output_path TEXT NOT NULL UNIQUE,
            last_content_hash TEXT NOT NULL,
            last_generated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY(catalog_item_id) REFERENCES catalog_items(internal_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_output_generated_files_catalog ON output_generated_files(catalog_item_id);
        CREATE INDEX IF NOT EXISTS idx_output_generated_files_type ON output_generated_files(output_type);

        CREATE TABLE IF NOT EXISTS output_run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            output_id TEXT NOT NULL,
            output_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_output_run_history_type ON output_run_history(output_type, started_at);
        """
    )
    conn.commit()
    if owns_conn:
        conn.close()


def _settings_store() -> JsonStore:
    settings = get_settings()
    return JsonStore(settings.data_dir / "outputs_strm_settings.json", StrmSettings().model_dump())


def _live_m3u_settings_store() -> JsonStore:
    settings = get_settings()
    return JsonStore(settings.data_dir / "outputs_live_m3u_settings.json", LiveM3uSettings().model_dump())


def get_strm_settings() -> StrmSettings:
    return StrmSettings(**_settings_store().read())


def get_live_m3u_settings() -> LiveM3uSettings:
    return LiveM3uSettings(**_live_m3u_settings_store().read())


def update_strm_settings(payload: StrmSettingsUpdate) -> StrmSettings:
    values = payload.model_dump(exclude_unset=True)
    candidate = StrmSettings(**{**get_strm_settings().model_dump(), **values})
    _validate_settings(candidate)
    return StrmSettings(**_settings_store().update(values))


def update_live_m3u_settings(payload: LiveM3uSettingsUpdate) -> LiveM3uSettings:
    values = payload.model_dump(exclude_unset=True)
    candidate = LiveM3uSettings(**{**get_live_m3u_settings().model_dump(), **values})
    _validate_live_m3u_settings(candidate)
    return LiveM3uSettings(**_live_m3u_settings_store().update(values))


def _validate_settings(settings: StrmSettings) -> None:
    for label, value in {
        "Movies STRM output directory": settings.movies_output_directory,
        "Series STRM output directory": settings.series_output_directory,
    }.items():
        if not value.strip():
            raise ValueError(f"{label} is required.")
        if not value.startswith("/"):
            raise ValueError(f"{label} must be a container absolute path.")
        if value.startswith("/Users/"):
            raise ValueError(f"{label} must use a Docker/container path, not a Mac host path.")


def _validate_live_m3u_settings(settings: LiveM3uSettings) -> None:
    path = settings.output_file_path.strip()
    if not path:
        raise ValueError("Live M3U output file path is required.")
    if not path.startswith("/"):
        raise ValueError("Live M3U output file path must be a container absolute path.")
    if path.startswith("/Users/"):
        raise ValueError("Live M3U output file path must use a Docker/container path, not a Mac host path.")
    if Path(path).suffix.lower() not in {".m3u", ".m3u8"}:
        raise ValueError("Live M3U output file path must end in .m3u or .m3u8.")


def _path_status(path: str, purpose: str, *, require_writable: bool, create: bool) -> OutputPathValidation:
    if path.startswith(("http://", "https://")):
        return OutputPathValidation(
            path=path,
            purpose=purpose,
            exists=True,
            readable=True,
            writable=False,
            can_create=False,
            status="ok",
            message="Remote URL configured; filesystem readability is not required.",
        )
    target = Path(path)
    exists = target.exists()
    readable = os.access(target, os.R_OK) if exists else False
    writable = os.access(target, os.W_OK) if exists else False
    can_create = False
    message = ""
    status = "ok"

    try:
        if not exists and create:
            target.mkdir(parents=True, exist_ok=True)
            exists = target.exists()
            message = "Directory was missing and has been created."
        elif not exists:
            parent = target.parent
            can_create = parent.exists() and os.access(parent, os.W_OK)
            message = "Path is missing."

        if exists:
            if not target.is_dir():
                status = "invalid"
                message = "Path exists but is not a directory."
            else:
                readable = os.access(target, os.R_OK)
                writable = os.access(target, os.W_OK)
                test_path = target / ".media-router-write-test"
                if require_writable and writable:
                    try:
                        test_path.write_text("ok\n")
                        test_path.unlink(missing_ok=True)
                    except OSError as exc:
                        writable = False
                        message = f"Write test failed: {exc}"
                if require_writable and not writable:
                    status = "error"
                    message = message or "Directory is not writable."
                elif not readable:
                    status = "error"
                    message = "Directory is not readable."
                else:
                    status = "ok"
                    message = message or ("Writable." if require_writable else "Readable.")
        elif can_create:
            status = "warning"
            message = "Directory is missing but parent is writable."
        else:
            status = "error"
            message = message or "Directory is missing and cannot be created."
    except OSError as exc:
        status = "error"
        message = f"Path check failed: {exc}"

    if exists:
        can_create = True
    return OutputPathValidation(
        path=str(target),
        purpose=purpose,
        exists=exists,
        readable=readable,
        writable=writable,
        can_create=can_create,
        status=status,
        message=message,
    )


def validate_strm_paths(create_missing: bool = True) -> OutputPathValidationResult:
    settings = get_strm_settings()
    _validate_settings(settings)
    live_settings = get_live_m3u_settings()
    app_settings = get_app_settings()
    checks = [
        _path_status(settings.movies_output_directory, "Movies STRM output directory", require_writable=True, create=create_missing),
        _path_status(settings.series_output_directory, "Series STRM output directory", require_writable=True, create=create_missing),
        _path_status(str(get_settings().data_dir), "Persistent data directory", require_writable=True, create=create_missing),
    ]
    if app_settings.iptv_boss_export_path.strip():
        checks.append(
            _path_status(app_settings.iptv_boss_export_path, "IPTVBoss import path", require_writable=False, create=False)
        )
    checks.extend(_output_overlap_warnings(settings, live_settings))
    can_generate = all(
        item.status == "ok"
        for item in checks
        if item.purpose in {"Movies STRM output directory", "Series STRM output directory", "Persistent data directory"}
    )
    summary = ", ".join(f"{item.purpose}: {item.status} ({item.path})" for item in checks)
    add_log("info", "outputs", f"STRM path validation completed: {summary}")
    return OutputPathValidationResult(paths=checks, can_generate=can_generate)


def _file_output_status(path: str, purpose: str, *, create_parent: bool) -> OutputPathValidation:
    target = Path(path)
    parent_result = _path_status(str(target.parent), f"{purpose} parent directory", require_writable=True, create=create_parent)
    exists = target.exists()
    readable = os.access(target, os.R_OK) if exists else parent_result.readable
    writable = os.access(target, os.W_OK) if exists else parent_result.writable
    can_create = parent_result.status == "ok"
    status = parent_result.status
    message = parent_result.message
    if parent_result.status == "ok":
        if exists and target.is_dir():
            status = "invalid"
            message = "Output file path points to a directory."
            readable = False
            writable = False
        elif exists and not os.access(target, os.W_OK):
            status = "error"
            message = "Output file exists but is not writable."
        elif exists:
            status = "ok"
            message = "Output file can be updated."
        else:
            status = "ok"
            message = "Output file can be created."
    return OutputPathValidation(
        path=str(target),
        purpose=purpose,
        exists=exists,
        readable=readable,
        writable=writable,
        can_create=can_create,
        status=status,
        message=message,
    )


def _resolved_path(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _overlap_warning(path: str, purpose: str, other_path: str, other_purpose: str) -> OutputPathValidation | None:
    if not path.strip() or not other_path.strip():
        return None
    current = _resolved_path(path)
    other = _resolved_path(other_path)
    if current == other or current in other.parents or other in current.parents:
        return OutputPathValidation(
            path=str(current),
            purpose=purpose,
            exists=current.exists(),
            readable=os.access(current, os.R_OK) if current.exists() else False,
            writable=os.access(current, os.W_OK) if current.exists() else False,
            can_create=current.exists() or (current.parent.exists() and os.access(current.parent, os.W_OK)),
            status="warning",
            message=f"Overlaps with {other_purpose}: {other}. This is allowed only if you intentionally configured shared output paths.",
        )
    return None


def _output_overlap_warnings(settings: StrmSettings, live_settings: LiveM3uSettings) -> list[OutputPathValidation]:
    live_parent = str(Path(live_settings.output_file_path).parent)
    candidates = [
        ("Movies STRM output directory overlap", settings.movies_output_directory, "Live M3U output parent directory", live_parent),
        ("Series STRM output directory overlap", settings.series_output_directory, "Live M3U output parent directory", live_parent),
        ("Movies and Series STRM output overlap", settings.movies_output_directory, "Series STRM output directory", settings.series_output_directory),
    ]
    warnings: list[OutputPathValidation] = []
    for purpose, path, other_purpose, other_path in candidates:
        warning = _overlap_warning(path, purpose, other_path, other_purpose)
        if warning is not None:
            warnings.append(warning)
    return warnings


def validate_live_m3u_paths(create_missing: bool = True) -> OutputPathValidationResult:
    settings = get_live_m3u_settings()
    _validate_live_m3u_settings(settings)
    checks = [
        _file_output_status(settings.output_file_path, "Live M3U output file", create_parent=create_missing),
        _path_status(str(get_settings().data_dir), "Persistent data directory", require_writable=True, create=create_missing),
    ]
    checks.extend(_output_overlap_warnings(get_strm_settings(), settings))
    can_generate = all(item.status not in {"error", "invalid"} for item in checks)
    summary = ", ".join(f"{item.purpose}: {item.status} ({item.path})" for item in checks)
    add_log("info", "outputs", f"Live M3U path validation completed: {summary}")
    return OutputPathValidationResult(paths=checks, can_generate=can_generate)


def _ensure_output_path_inside_root(output_path: Path, root: str) -> None:
    root_path = Path(root).resolve()
    resolved = output_path.resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise OutputPathError(f"Refusing to write outside configured output directory: {resolved}") from exc


def _sanitize_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:160] or fallback


def _runtime_url(item: CatalogItem, runtime_base_url: str) -> str:
    route = route_for_media_type(item.media_type)
    if route not in {"movie", "episode"}:
        raise ValueError("STRM output only supports movies and episodes in Sprint 6.")
    return f"{runtime_base_url}/r/{route}/{item.internal_id}"


def _movie_output_path(item: CatalogItem, settings: StrmSettings) -> Path:
    title = _sanitize_segment(item.title, item.internal_id)
    return Path(settings.movies_output_directory) / f"{title}.strm"


def _episode_output_path(item: CatalogItem, settings: StrmSettings) -> Path:
    series_name = _sanitize_segment(item.show_name or item.group_title or item.title, item.internal_id)
    season_number = int(item.season_number or 0)
    episode_number = int(item.episode_number or 0)
    season_folder = f"Season {season_number:02d}" if season_number else "Season Unknown"
    if item.confidence == "low" or not season_number or not episode_number:
        filename = f"{_sanitize_segment(item.title, item.internal_id)} [{item.internal_id}].strm"
    else:
        episode_title = _sanitize_segment(item.episode_title or item.title, "")
        suffix = f" - {episode_title}" if episode_title else ""
        filename = f"{series_name} - S{season_number:02d}E{episode_number:02d}{suffix}.strm"
    return Path(settings.series_output_directory) / series_name / season_folder / filename


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _xml_attr(value: str | None) -> str:
    return (value or "").replace("&", "&amp;").replace('"', "&quot;")


def _live_runtime_base(settings: LiveM3uSettings, request_base_url: str | None) -> str:
    configured = settings.runtime_client_access_url.strip().rstrip("/")
    return configured or public_runtime_base_url(request_base_url)


def _enabled_source_counts(catalog_item_ids: list[str]) -> dict[str, int]:
    if not catalog_item_ids:
        return {}
    placeholders = ",".join("?" for _ in catalog_item_ids)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT catalog_internal_id, COUNT(*) AS count
            FROM source_availability
            WHERE enabled = 1
              AND media_type = 'channel'
              AND catalog_internal_id IN ({placeholders})
            GROUP BY catalog_internal_id
            """,
            tuple(catalog_item_ids),
        ).fetchall()
    return {row["catalog_internal_id"]: row["count"] for row in rows}


def _live_m3u_entry(item: CatalogItem, settings: LiveM3uSettings, runtime_base_url: str) -> LiveM3uPreviewEntry:
    attrs: list[str] = []
    if item.tvg_chno:
        attrs.append(f'tvg-chno="{_xml_attr(item.tvg_chno)}"')
    if settings.include_tvg_id and item.tvg_id:
        attrs.append(f'tvg-id="{_xml_attr(item.tvg_id)}"')
    if settings.include_tvg_name and item.tvg_name:
        attrs.append(f'tvg-name="{_xml_attr(item.tvg_name)}"')
    if settings.include_logos and item.tvg_logo:
        attrs.append(f'tvg-logo="{_xml_attr(item.tvg_logo)}"')
    if settings.include_group_title and item.group_title:
        attrs.append(f'group-title="{_xml_attr(item.group_title)}"')
    attr_text = f" {' '.join(attrs)}" if attrs else ""
    display_title = item.tvg_name or item.title
    extinf = f'#EXTINF:-1{attr_text},{display_title}'
    runtime_url = f"{runtime_base_url}/r/live/{item.internal_id}"
    return LiveM3uPreviewEntry(
        catalog_item_id=item.internal_id,
        title=display_title,
        channel_number=item.tvg_chno,
        group_title=item.group_title,
        extinf=extinf,
        runtime_url=runtime_url,
    )


def _channel_number_sort_key(value: str | None) -> tuple[int, float, str]:
    if not value:
        return (1, 0, "")
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        return (1, 0, value.lower())
    return (0, float(match.group(0)), value.lower())


def _live_channel_sort_key(entry: LiveM3uPreviewEntry) -> tuple:
    return (
        *_channel_number_sort_key(entry.channel_number),
        (entry.group_title or "").lower(),
        entry.title.lower(),
        entry.catalog_item_id,
    )


def _live_m3u_content(entries: list[LiveM3uPreviewEntry]) -> str:
    lines = ["#EXTM3U"]
    for entry in entries:
        lines.append(entry.extinf)
        lines.append(entry.runtime_url)
    return "\n".join(lines) + "\n"


def _tracked_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM output_generated_files
        WHERE output_type = ?
        ORDER BY last_generated_at DESC, output_path
        LIMIT ?
        """,
        (OUTPUT_TYPE, MAX_OUTPUT_ROWS),
    ).fetchall()


def _generated_from_row(row: sqlite3.Row) -> GeneratedOutputFile:
    return GeneratedOutputFile(
        output_id=row["output_id"],
        catalog_item_id=row["catalog_item_id"],
        media_type=row["media_type"],
        output_type=row["output_type"],
        output_path=row["output_path"],
        last_content_hash=row["last_content_hash"],
        last_generated_at=datetime.fromisoformat(row["last_generated_at"]),
        status=row["status"],
    )


def _summary_from_counts(mode: str, counts: dict[str, int], output_paths: list[str], duration_seconds: float) -> StrmOutputSummary:
    return StrmOutputSummary(
        mode=mode,
        created_count=counts.get("create", 0),
        updated_count=counts.get("update", 0),
        skipped_count=counts.get("skip", 0),
        removed_count=counts.get("remove", 0),
        failed_count=counts.get("fail", 0),
        movie_count=counts.get("movie", 0),
        episode_count=counts.get("episode", 0),
        output_paths=output_paths,
        duration_seconds=round(duration_seconds, 3),
    )


def _record_generated_file(conn: sqlite3.Connection, item: CatalogItem, output_path: Path, content_hash: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO output_generated_files (
            output_id, catalog_item_id, media_type, output_type, output_path,
            last_content_hash, last_generated_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'generated')
        ON CONFLICT(output_path) DO UPDATE SET
            output_id=excluded.output_id,
            catalog_item_id=excluded.catalog_item_id,
            media_type=excluded.media_type,
            output_type=excluded.output_type,
            last_content_hash=excluded.last_content_hash,
            last_generated_at=excluded.last_generated_at,
            status='generated'
        """,
        (OUTPUT_ID, item.internal_id, item.media_type, OUTPUT_TYPE, str(output_path), content_hash, now),
    )


def _record_run(conn: sqlite3.Connection, mode: str, status: str, summary: StrmOutputSummary, started_at: str, finished_at: str) -> None:
    conn.execute(
        """
        INSERT INTO output_run_history (
            output_id, output_type, mode, status, summary_json, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (OUTPUT_ID, OUTPUT_TYPE, mode, status, summary.model_dump_json(), started_at, finished_at),
    )


def _record_live_m3u_run(conn: sqlite3.Connection, mode: str, status: str, summary: LiveM3uOutputSummary, started_at: str, finished_at: str) -> None:
    conn.execute(
        """
        INSERT INTO output_run_history (
            output_id, output_type, mode, status, summary_json, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (LIVE_M3U_OUTPUT_ID, LIVE_M3U_OUTPUT_TYPE, mode, status, summary.model_dump_json(), started_at, finished_at),
    )


def _operation(action: str, item: CatalogItem | None, output_path: Path, runtime_url: str | None, reason: str) -> StrmOutputOperation:
    return StrmOutputOperation(
        action=action,
        media_type=item.media_type if item else "unknown",
        catalog_item_id=item.internal_id if item else None,
        title=item.title if item else None,
        output_path=str(output_path),
        runtime_url=runtime_url,
        reason=reason,
    )


def build_strm_outputs(request_base_url: str | None = None, dry_run: bool = True) -> StrmOutputResult:
    started = time.monotonic()
    started_at = datetime.utcnow().isoformat()
    settings = get_strm_settings()
    _validate_settings(settings)
    effective_dry_run = dry_run or settings.dry_run_mode
    mode = "dry_run" if effective_dry_run else "generate"
    runtime_base_url = public_runtime_base_url(request_base_url)
    add_log("info", "outputs", f"STRM {mode} started for movies={settings.movies_output_directory}, series={settings.series_output_directory}")
    if not effective_dry_run:
        validation = validate_strm_paths(create_missing=True)
        failed = [item for item in validation.paths if item.purpose in {"Movies STRM output directory", "Series STRM output directory", "Persistent data directory"} and item.status != "ok"]
        if failed:
            detail = "; ".join(f"{item.purpose} {item.path}: {item.message}" for item in failed)
            raise OutputPathError(f"STRM output path validation failed: {detail}")
    movies = list_items("movie", limit=500, offset=0)
    episodes = list_items("episode", limit=500, offset=0)
    items = movies + episodes
    counts = {"movie": len(movies), "episode": len(episodes)}
    operations: list[StrmOutputOperation] = []
    output_paths: list[str] = [settings.movies_output_directory, settings.series_output_directory]
    desired_paths: set[str] = set()
    seen_paths: dict[str, int] = {}

    with _connect() as conn:
        for item in items:
            try:
                output_path = _movie_output_path(item, settings) if item.media_type == "movie" else _episode_output_path(item, settings)
                root = settings.movies_output_directory if item.media_type == "movie" else settings.series_output_directory
                _ensure_output_path_inside_root(output_path, root)
                if str(output_path) in seen_paths:
                    seen_paths[str(output_path)] += 1
                    output_path = output_path.with_name(f"{output_path.stem} [{item.internal_id}]{output_path.suffix}")
                    _ensure_output_path_inside_root(output_path, root)
                else:
                    seen_paths[str(output_path)] = 1
                desired_paths.add(str(output_path))
                runtime_url = _runtime_url(item, runtime_base_url)
                content = f"{runtime_url}\n"
                digest = _content_hash(content)
                try:
                    existing_content = output_path.read_text() if output_path.exists() else None
                except OSError as exc:
                    raise OutputPathError(f"Could not read existing STRM file at {output_path}: {exc}") from exc
                if existing_content == content:
                    action = "skip"
                    reason = "Existing STRM content is unchanged."
                elif output_path.exists() and not settings.overwrite_existing_files:
                    action = "skip"
                    reason = "File exists and overwrite is disabled."
                elif output_path.exists():
                    action = "update"
                    reason = "Existing STRM content would be updated." if effective_dry_run else "Existing STRM content updated."
                else:
                    action = "create"
                    reason = "STRM file would be created." if effective_dry_run else "STRM file created."

                if not effective_dry_run and action in {"create", "update"}:
                    try:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(content)
                    except OSError as exc:
                        raise OutputPathError(f"Could not write STRM file at {output_path}: {exc}") from exc
                    _record_generated_file(conn, item, output_path, digest, datetime.utcnow().isoformat())
                elif not effective_dry_run and action == "skip" and output_path.exists() and existing_content == content:
                    _record_generated_file(conn, item, output_path, digest, datetime.utcnow().isoformat())

                counts[action] = counts.get(action, 0) + 1
                operations.append(_operation(action, item, output_path, runtime_url, reason))
            except Exception as exc:
                counts["fail"] = counts.get("fail", 0) + 1
                failed_path = output_path if "output_path" in locals() else Path("")
                operations.append(_operation("fail", item, failed_path, None, f"Failed to prepare STRM output: {exc}"))
                UVICORN_LOGGER.exception("STRM %s failed for catalog item %s at %s", mode, item.internal_id, failed_path)

        if settings.remove_orphaned_files:
            current_ids = {item.internal_id for item in items}
            for row in _tracked_files(conn):
                tracked_path = Path(row["output_path"])
                if row["catalog_item_id"] in current_ids and row["output_path"] in desired_paths:
                    continue
                reason = "Tracked generated file is no longer produced by current catalog/settings."
                if not effective_dry_run and tracked_path.exists():
                    try:
                        tracked_path.unlink()
                    except OSError as exc:
                        counts["fail"] = counts.get("fail", 0) + 1
                        operations.append(
                            StrmOutputOperation(
                                action="fail",
                                media_type=row["media_type"],
                                catalog_item_id=row["catalog_item_id"],
                                title=None,
                                output_path=row["output_path"],
                                runtime_url=None,
                                reason=f"Could not remove tracked orphaned STRM file: {exc}",
                            )
                        )
                        UVICORN_LOGGER.exception("STRM orphan cleanup failed at %s", tracked_path)
                        continue
                if not effective_dry_run:
                    conn.execute("UPDATE output_generated_files SET status = 'removed' WHERE output_path = ?", (row["output_path"],))
                counts["remove"] = counts.get("remove", 0) + 1
                operations.append(
                    StrmOutputOperation(
                        action="remove",
                        media_type=row["media_type"],
                        catalog_item_id=row["catalog_item_id"],
                        title=None,
                        output_path=row["output_path"],
                        runtime_url=None,
                        reason=reason if effective_dry_run else "Tracked generated file removed.",
                    )
                )

        summary = _summary_from_counts(mode, counts, output_paths, time.monotonic() - started)
        status = "complete" if summary.failed_count == 0 else "failed"
        finished_at = datetime.utcnow().isoformat()
        if not effective_dry_run:
            conn.commit()
        _record_run(conn, mode, status, summary, started_at, finished_at)
        conn.commit()

    add_log(
        "info" if summary.failed_count == 0 else "error",
        "outputs",
        (
            f"STRM {mode} completed: {summary.created_count} created, {summary.updated_count} updated, "
            f"{summary.skipped_count} skipped, {summary.removed_count} removed, {summary.failed_count} failed"
        ),
    )
    return StrmOutputResult(settings=settings, runtime_base_url=runtime_base_url, summary=summary, operations=operations)


def dry_run_strm_outputs(request_base_url: str | None = None) -> StrmOutputResult:
    return build_strm_outputs(request_base_url=request_base_url, dry_run=True)


def generate_strm_outputs(request_base_url: str | None = None) -> StrmOutputResult:
    return build_strm_outputs(request_base_url=request_base_url, dry_run=False)


def build_live_m3u_output(request_base_url: str | None = None, dry_run: bool = True) -> LiveM3uOutputResult:
    started = time.monotonic()
    started_at = datetime.utcnow().isoformat()
    settings = get_live_m3u_settings()
    _validate_live_m3u_settings(settings)
    effective_dry_run = dry_run or settings.dry_run_mode
    mode = "dry_run" if effective_dry_run else "generate"
    runtime_base_url = _live_runtime_base(settings, request_base_url)
    output_path = Path(settings.output_file_path)
    add_log("info", "outputs", f"Live M3U {mode} started for output={settings.output_file_path}")
    if not effective_dry_run:
        validation = validate_live_m3u_paths(create_missing=True)
        failed = [item for item in validation.paths if item.status in {"error", "invalid"}]
        if failed:
            detail = "; ".join(f"{item.purpose} {item.path}: {item.message}" for item in failed)
            raise OutputPathError(f"Live M3U output path validation failed: {detail}")

    channels = list_items("channel", limit=settings.channel_limit or 500, offset=0)
    source_counts = _enabled_source_counts([item.internal_id for item in channels])
    entries: list[LiveM3uPreviewEntry] = []
    skipped: list[str] = []
    for item in channels:
        if not settings.include_disabled_channels and source_counts.get(item.internal_id, 0) < 1:
            skipped.append(f"{item.title} ({item.internal_id}) skipped: no enabled source availability.")
            continue
        entries.append(_live_m3u_entry(item, settings, runtime_base_url))
    entries.sort(key=_live_channel_sort_key)

    content = _live_m3u_content(entries)
    digest = _content_hash(content)
    created_count = updated_count = failed_count = 0
    existing_content = None
    try:
        existing_content = output_path.read_text() if output_path.exists() else None
        if not effective_dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if existing_content != content:
                output_path.write_text(content)
                created_count = 1 if existing_content is None else 0
                updated_count = 0 if existing_content is None else 1
    except OSError as exc:
        failed_count = 1
        UVICORN_LOGGER.exception("Live M3U %s failed at %s", mode, output_path)
        if not effective_dry_run:
            raise OutputPathError(f"Could not write Live M3U file at {output_path}: {exc}") from exc

    summary = LiveM3uOutputSummary(
        mode=mode,
        total_live_channels=len(channels),
        written_count=len(entries),
        skipped_count=len(skipped),
        created_count=created_count,
        updated_count=updated_count,
        failed_count=failed_count,
        output_path=str(output_path),
        duration_seconds=round(time.monotonic() - started, 3),
    )
    if effective_dry_run and existing_content == content:
        summary.created_count = 0
        summary.updated_count = 0
    elif effective_dry_run:
        summary.created_count = 1 if not output_path.exists() else 0
        summary.updated_count = 0 if not output_path.exists() else 1

    status = "complete" if summary.failed_count == 0 else "failed"
    finished_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        _record_live_m3u_run(conn, mode, status, summary, started_at, finished_at)
        if not effective_dry_run and summary.failed_count == 0 and entries:
            conn.execute(
                """
                INSERT INTO output_generated_files (
                    output_id, catalog_item_id, media_type, output_type, output_path,
                    last_content_hash, last_generated_at, status
                ) VALUES (?, ?, 'channel', ?, ?, ?, ?, 'generated')
                ON CONFLICT(output_path) DO UPDATE SET
                    output_id=excluded.output_id,
                    catalog_item_id=excluded.catalog_item_id,
                    media_type='channel',
                    output_type=excluded.output_type,
                    last_content_hash=excluded.last_content_hash,
                    last_generated_at=excluded.last_generated_at,
                    status='generated'
                """,
                (LIVE_M3U_OUTPUT_ID, entries[0].catalog_item_id if entries else "live_m3u", LIVE_M3U_OUTPUT_TYPE, str(output_path), digest, datetime.utcnow().isoformat()),
            )
        conn.commit()
    add_log(
        "info" if summary.failed_count == 0 else "error",
        "outputs",
        (
            f"Live M3U {mode} completed: {summary.written_count} channels written, "
            f"{summary.skipped_count} skipped, {summary.created_count} created, "
            f"{summary.updated_count} updated, {summary.failed_count} failed"
        ),
    )
    return LiveM3uOutputResult(
        settings=settings,
        runtime_base_url=runtime_base_url,
        summary=summary,
        preview_entries=entries[:10],
        skipped_channels=skipped[:50],
    )


def dry_run_live_m3u_output(request_base_url: str | None = None) -> LiveM3uOutputResult:
    return build_live_m3u_output(request_base_url=request_base_url, dry_run=True)


def generate_live_m3u_output(request_base_url: str | None = None) -> LiveM3uOutputResult:
    return build_live_m3u_output(request_base_url=request_base_url, dry_run=False)


def run_strm_generate_job(job_id: str, request_base_url: str | None = None) -> None:
    update_job(job_id, status="running", progress=10, message="Preparing STRM output generation")
    add_log("info", "outputs", f"STRM generate job {job_id} started")
    try:
        result = generate_strm_outputs(request_base_url)
        failed_operations = [operation for operation in result.operations if operation.action == "fail"]
        failure_reason = failed_operations[0].reason if failed_operations else ""
        status = "complete" if result.summary.failed_count == 0 else "failed"
        message = (
            f"STRM generation complete: {result.summary.created_count} created, "
            f"{result.summary.updated_count} updated, {result.summary.skipped_count} skipped, "
            f"{result.summary.removed_count} removed, {result.summary.failed_count} failed"
        )
        if failure_reason:
            message = f"STRM generation failed: {failure_reason}"
        update_job(
            job_id,
            status=status,
            progress=100,
            message=message,
            result={**result.summary.model_dump(), "failure_reason": failure_reason or None},
        )
        add_log("info" if status == "complete" else "error", "outputs", message)
    except Exception as exc:
        UVICORN_LOGGER.exception("STRM generate job %s failed", job_id)
        update_job(job_id, status="failed", progress=100, message=f"STRM generation failed: {exc}", result={"failed_count": 1, "failure_reason": str(exc)})
        add_log("error", "outputs", f"STRM generation failed: {exc}")


def run_live_m3u_generate_job(job_id: str, request_base_url: str | None = None) -> None:
    update_job(job_id, status="running", progress=10, message="Preparing Live M3U output generation")
    add_log("info", "outputs", f"Live M3U generate job {job_id} started")
    try:
        result = generate_live_m3u_output(request_base_url)
        status = "complete" if result.summary.failed_count == 0 else "failed"
        message = (
            f"Live M3U generation complete: {result.summary.written_count} channels written, "
            f"{result.summary.skipped_count} skipped"
        )
        update_job(
            job_id,
            status=status,
            progress=100,
            message=message,
            result=result.summary.model_dump(),
        )
        add_log("info" if status == "complete" else "error", "outputs", message)
    except Exception as exc:
        UVICORN_LOGGER.exception("Live M3U generate job %s failed", job_id)
        update_job(job_id, status="failed", progress=100, message=f"Live M3U generation failed: {exc}", result={"failed_count": 1, "failure_reason": str(exc)})
        add_log("error", "outputs", f"Live M3U generation failed: {exc}")


def list_generated_files() -> list[GeneratedOutputFile]:
    with _connect() as conn:
        rows = _tracked_files(conn)
    return [_generated_from_row(row) for row in rows]


def _history_from_row(row: sqlite3.Row) -> OutputRunHistory:
    summary_data = json.loads(row["summary_json"])
    return OutputRunHistory(
        output_id=row["output_id"],
        output_type=row["output_type"],
        mode=row["mode"],
        status=row["status"],
        summary=StrmOutputSummary(**summary_data),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
    )


def list_output_history(limit: int = 25) -> list[OutputRunHistory]:
    limit = min(max(limit, 1), 100)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM output_run_history
            WHERE output_type = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (OUTPUT_TYPE, limit),
        ).fetchall()
    return [_history_from_row(row) for row in rows]


def _live_m3u_history_from_row(row: sqlite3.Row) -> LiveM3uRunHistory:
    summary_data = json.loads(row["summary_json"])
    return LiveM3uRunHistory(
        output_id=row["output_id"],
        output_type=row["output_type"],
        mode=row["mode"],
        status=row["status"],
        summary=LiveM3uOutputSummary(**summary_data),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
    )


def list_live_m3u_history(limit: int = 25) -> list[LiveM3uRunHistory]:
    limit = min(max(limit, 1), 100)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM output_run_history
            WHERE output_type = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (LIVE_M3U_OUTPUT_TYPE, limit),
        ).fetchall()
    return [_live_m3u_history_from_row(row) for row in rows]


def preview_live_m3u_output(request_base_url: str | None = None) -> LiveM3uOutputResult:
    return dry_run_live_m3u_output(request_base_url)
