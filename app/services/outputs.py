from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from app.core.config import get_settings
from app.core.json_store import JsonStore
from app.schemas.catalog import CatalogItem
from app.schemas.outputs import (
    GeneratedOutputFile,
    LiveM3uOutputResult,
    LiveM3uEstimate,
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
from app.services.catalog import ensure_schema, get_summary, list_items
from app.services.jobs import clear_job_cancel_request, is_job_cancel_requested, update_job
from app.services.logs import add_log
from app.services.runtime import public_runtime_base_url, route_for_media_type
from app.services.settings import get_app_settings


OUTPUT_TYPE = "strm"
OUTPUT_ID = "strm"
LIVE_M3U_OUTPUT_TYPE = "live-m3u"
LIVE_M3U_OUTPUT_ID = "live-m3u"
MAX_OUTPUT_ROWS = 500
MAX_OPERATION_PREVIEW = 500
STRM_GENERATION_PRESETS = {"Test": (500, 500), "Small": (2000, 2000), "Medium": (5000, 10000)}
LIVE_GENERATION_PRESETS = {"Test": 500, "Small": 2000, "Medium": 5000}
UVICORN_LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class _StrmFilePlan:
    item: CatalogItem
    output_path: Path
    runtime_url: str
    content: str
    content_bytes: bytes
    content_hash: str
    tracking_current: bool
    tracked_at_timestamp: float | None


@dataclass(frozen=True)
class _StrmFileResult:
    plan: _StrmFilePlan
    action: str
    reason: str
    track: bool
    check_seconds: float
    write_seconds: float
    error: Exception | None = None


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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(output_generated_files)").fetchall()}
    if "generation_run_id" not in columns:
        conn.execute("ALTER TABLE output_generated_files ADD COLUMN generation_run_id TEXT")
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
    store = _live_m3u_settings_store()
    stored_values: dict = {}
    if store.path.exists():
        try:
            stored_values = json.loads(store.path.read_text())
        except (json.JSONDecodeError, OSError):
            stored_values = {}
    values = store.read()
    if "generation_mode" not in stored_values:
        legacy_limit = int(stored_values.get("channel_limit") or 0)
        values["generation_mode"] = "Custom" if legacy_limit > 0 and legacy_limit != 500 else "Test"
        values["maximum_live_channels"] = legacy_limit if legacy_limit > 0 else 500
    values["channel_limit"] = values.get("maximum_live_channels", 500)
    return LiveM3uSettings(**values)


def update_strm_settings(payload: StrmSettingsUpdate) -> StrmSettings:
    values = payload.model_dump(exclude_unset=True)
    mode = values.get("generation_mode", get_strm_settings().generation_mode)
    if mode in STRM_GENERATION_PRESETS:
        values["maximum_movies"], values["maximum_episodes"] = STRM_GENERATION_PRESETS[mode]
    elif mode == "Unlimited":
        values["maximum_movies"] = 0
        values["maximum_episodes"] = 0
    candidate = StrmSettings(**{**get_strm_settings().model_dump(), **values})
    _validate_settings(candidate)
    return StrmSettings(**_settings_store().update(values))


def update_live_m3u_settings(payload: LiveM3uSettingsUpdate) -> LiveM3uSettings:
    values = payload.model_dump(exclude_unset=True)
    current = get_live_m3u_settings()
    mode = values.get("generation_mode", current.generation_mode)
    if mode in LIVE_GENERATION_PRESETS:
        values["maximum_live_channels"] = LIVE_GENERATION_PRESETS[mode]
    elif mode == "Unlimited":
        values["maximum_live_channels"] = 0
    elif "maximum_live_channels" not in values and "channel_limit" in values:
        values["maximum_live_channels"] = values["channel_limit"]
    values["channel_limit"] = values.get("maximum_live_channels", current.maximum_live_channels)
    candidate = LiveM3uSettings(**{**current.model_dump(), **values})
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
    if settings.generation_mode == "Custom" and (settings.maximum_movies <= 0 or settings.maximum_episodes <= 0):
        raise ValueError("Custom mode requires positive maximum movie and episode limits.")
    if settings.generation_mode != "Unlimited" and (settings.maximum_movies <= 0 or settings.maximum_episodes <= 0):
        raise ValueError("Zero or blank limits are allowed only in explicitly selected Unlimited mode.")


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
    if settings.generation_mode == "Custom" and settings.maximum_live_channels <= 0:
        raise ValueError("Custom mode requires a positive maximum live channel limit.")
    if settings.generation_mode != "Unlimited" and settings.maximum_live_channels <= 0:
        raise ValueError("Zero or blank live channel limits are allowed only in explicitly selected Unlimited mode.")


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


def get_live_m3u_estimate() -> LiveM3uEstimate:
    settings = get_live_m3u_settings()
    with _connect() as conn:
        placement_filter = """p.active=1 AND (
            p.source_identity != 'legacy-canonical' OR NOT EXISTS (
                SELECT 1 FROM channel_placements real WHERE real.catalog_item_id=p.catalog_item_id
                AND real.active=1 AND real.source_identity != 'legacy-canonical'))"""
        total = conn.execute(f"SELECT COUNT(*) AS count FROM channel_placements p WHERE {placement_filter}").fetchone()["count"]
        eligible_filter = "" if settings.include_disabled_channels else """ AND EXISTS (
            SELECT 1 FROM source_availability s WHERE s.catalog_internal_id=p.catalog_item_id
            AND s.media_type='channel' AND s.enabled=1)"""
        eligible = conn.execute(f"SELECT COUNT(*) AS count FROM channel_placements p WHERE {placement_filter}{eligible_filter}").fetchone()["count"]
    limit = None if settings.generation_mode == "Unlimited" else settings.maximum_live_channels
    included = eligible if limit is None else min(eligible, limit)
    return LiveM3uEstimate(total_live_channels=total, eligible_live_channels=eligible,
        configured_limit=limit, included_channels=included, excluded_by_limit=eligible-included,
        capped=settings.generation_mode != "Unlimited")


def _live_m3u_entry(item: CatalogItem, settings: LiveM3uSettings, runtime_base_url: str) -> LiveM3uPreviewEntry:
    attrs: list[str] = []
    attrs.append(f'media-router-id="{_xml_attr(item.internal_id)}"')
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
    runtime_url = f"{runtime_base_url}/r/live/{item.internal_id}?mr_catalog_id={item.internal_id}"
    return LiveM3uPreviewEntry(
        catalog_item_id=item.internal_id,
        title=display_title,
        channel_number=item.tvg_chno,
        group_title=item.group_title,
        extinf=extinf,
        runtime_url=runtime_url,
    )


def _live_m3u_placement_entry(row: sqlite3.Row, settings: LiveM3uSettings, runtime_base_url: str) -> LiveM3uPreviewEntry:
    attrs: list[str] = [f'media-router-id="{_xml_attr(row["catalog_item_id"])}"']
    if row["channel_number"]:
        attrs.append(f'tvg-chno="{_xml_attr(row["channel_number"])}"')
    if settings.include_tvg_id and row["tvg_id"]:
        attrs.append(f'tvg-id="{_xml_attr(row["tvg_id"])}"')
    if settings.include_tvg_name and row["tvg_name"]:
        attrs.append(f'tvg-name="{_xml_attr(row["tvg_name"])}"')
    if settings.include_logos and row["tvg_logo"]:
        attrs.append(f'tvg-logo="{_xml_attr(row["tvg_logo"])}"')
    if settings.include_group_title and row["group_title"]:
        attrs.append(f'group-title="{_xml_attr(row["group_title"])}"')
    attr_text = f" {' '.join(attrs)}" if attrs else ""
    title = row["display_title"]
    return LiveM3uPreviewEntry(
        catalog_item_id=row["catalog_item_id"], title=title,
        channel_number=row["channel_number"], group_title=row["group_title"],
        extinf=f"#EXTINF:-1{attr_text},{title}",
        runtime_url=f"{runtime_base_url}/r/live/{row['catalog_item_id']}?mr_catalog_id={row['catalog_item_id']}",
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


def _summary_from_counts(mode: str, counts: dict[str, int], output_paths: list[str], duration_seconds: float, settings: StrmSettings, total_items: int = 0, excluded: int = 0) -> StrmOutputSummary:
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
        total_items=total_items,
        processed_items=counts.get("movie", 0) + counts.get("episode", 0),
        excluded_by_limits=excluded,
        capped=settings.generation_mode != "Unlimited",
        generation_mode=settings.generation_mode,
        batch_size=settings.batch_size,
        worker_count=settings.worker_count,
        items_per_second=round((counts.get("movie", 0) + counts.get("episode", 0)) / duration_seconds, 2) if duration_seconds > 0 else 0,
        average_ms_per_item=round(duration_seconds * 1000 / (counts.get("movie", 0) + counts.get("episode", 0)), 3) if counts.get("movie", 0) + counts.get("episode", 0) else 0,
    )


def _tracked_hashes_for_batch(conn: sqlite3.Connection, item_ids: list[str]) -> dict[tuple[str, str], tuple[str, float | None]]:
    if not item_ids:
        return {}
    placeholders = ",".join("?" for _ in item_ids)
    rows = conn.execute(
        f"""SELECT catalog_item_id, output_path, last_content_hash, last_generated_at
        FROM output_generated_files
        WHERE output_type=? AND status='generated' AND catalog_item_id IN ({placeholders})""",
        (OUTPUT_TYPE, *item_ids),
    ).fetchall()
    tracked = {}
    for row in rows:
        try:
            generated_at = datetime.fromisoformat(row["last_generated_at"])
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=timezone.utc)
            generated_timestamp = generated_at.timestamp()
        except (TypeError, ValueError):
            generated_timestamp = None
        tracked[(row["catalog_item_id"], row["output_path"])] = (row["last_content_hash"], generated_timestamp)
    return tracked


def _record_generated_files(conn: sqlite3.Connection, results: list[_StrmFileResult], now: str, run_id: str) -> None:
    rows = [
        (OUTPUT_ID, result.plan.item.internal_id, result.plan.item.media_type, OUTPUT_TYPE,
         str(result.plan.output_path), result.plan.content_hash, now, run_id)
        for result in results if result.track and result.error is None
    ]
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO output_generated_files (
            output_id, catalog_item_id, media_type, output_type, output_path,
            last_content_hash, last_generated_at, status, generation_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'generated', ?)
        ON CONFLICT(output_path) DO UPDATE SET
            output_id=excluded.output_id,
            catalog_item_id=excluded.catalog_item_id,
            media_type=excluded.media_type,
            output_type=excluded.output_type,
            last_content_hash=excluded.last_content_hash,
            last_generated_at=excluded.last_generated_at,
            status='generated',
            generation_run_id=excluded.generation_run_id
        """,
        rows,
    )


def _atomic_write_strm(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _process_strm_file(plan: _StrmFilePlan, *, dry_run: bool, overwrite: bool) -> _StrmFileResult:
    check_started = time.monotonic()
    write_seconds = 0.0
    try:
        try:
            file_stat = plan.output_path.stat()
            exists = True
        except FileNotFoundError:
            file_stat = None
            exists = False
        unchanged = bool(exists and plan.tracking_current and plan.tracked_at_timestamp is not None
                         and file_stat and file_stat.st_mtime <= plan.tracked_at_timestamp)
        if exists and not unchanged:
            try:
                unchanged = plan.output_path.stat().st_size == len(plan.content_bytes) and plan.output_path.read_bytes() == plan.content_bytes
            except OSError as exc:
                raise OutputPathError(f"Could not inspect existing STRM file at {plan.output_path}: {exc}") from exc
        check_seconds = time.monotonic() - check_started
        if unchanged:
            return _StrmFileResult(plan, "skip", "Existing STRM content is unchanged.", not dry_run,
                                   check_seconds, write_seconds)
        if exists and not overwrite:
            return _StrmFileResult(plan, "skip", "File exists and overwrite is disabled.", False,
                                   check_seconds, write_seconds)
        action = "update" if exists else "create"
        reason = ("Existing STRM content would be updated." if exists else "STRM file would be created.") if dry_run else ("Existing STRM content updated." if exists else "STRM file created.")
        if not dry_run:
            write_started = time.monotonic()
            _atomic_write_strm(plan.output_path, plan.content_bytes)
            write_seconds = time.monotonic() - write_started
        return _StrmFileResult(plan, action, reason, not dry_run, check_seconds, write_seconds)
    except Exception as exc:
        return _StrmFileResult(plan, "fail", f"Failed to prepare STRM output: {exc}", False,
                               time.monotonic() - check_started, write_seconds, exc)


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


def build_strm_outputs(request_base_url: str | None = None, dry_run: bool = True, job_id: str | None = None) -> StrmOutputResult:
    started = time.monotonic()
    started_at = datetime.utcnow().isoformat()
    settings = get_strm_settings()
    _validate_settings(settings)
    effective_dry_run = dry_run or settings.dry_run_mode
    mode = "dry_run" if effective_dry_run else "generate"
    runtime_base_url = public_runtime_base_url(request_base_url)
    limits = {"movie": None if settings.generation_mode == "Unlimited" else settings.maximum_movies,
              "episode": None if settings.generation_mode == "Unlimited" else settings.maximum_episodes}
    catalog_summary = get_summary()
    catalog_totals = {"movie": catalog_summary.movies, "episode": catalog_summary.episodes}
    selected_totals = {kind: total if limits[kind] is None else min(total, limits[kind]) for kind, total in catalog_totals.items()}
    total_items = sum(selected_totals.values())
    excluded = sum(catalog_totals.values()) - total_items
    add_log("info", "outputs", f"STRM {mode} started: generation_mode={settings.generation_mode}, maximum_movies={settings.maximum_movies}, maximum_episodes={settings.maximum_episodes}, batch_size={settings.batch_size}, worker_count={settings.worker_count}")
    if not effective_dry_run:
        validation = validate_strm_paths(create_missing=True)
        failed = [item for item in validation.paths if item.purpose in {"Movies STRM output directory", "Series STRM output directory", "Persistent data directory"} and item.status != "ok"]
        if failed:
            detail = "; ".join(f"{item.purpose} {item.path}: {item.message}" for item in failed)
            raise OutputPathError(f"STRM output path validation failed: {detail}")
    counts = {"movie": 0, "episode": 0}
    operations: list[StrmOutputOperation] = []
    output_paths: list[str] = [settings.movies_output_directory, settings.series_output_directory]
    run_id = uuid4().hex
    cancelled = False
    batch_number = 0

    with _connect() as conn, ThreadPoolExecutor(max_workers=settings.worker_count, thread_name_prefix="strm-write") as executor:
        conn.execute("CREATE TEMP TABLE strm_desired_paths (output_path TEXT PRIMARY KEY, catalog_item_id TEXT NOT NULL)")
        conn.execute("CREATE TEMP TABLE strm_selected_ids (catalog_item_id TEXT PRIMARY KEY)")
        for media_type in ("movie", "episode"):
            offset = 0
            while offset < selected_totals[media_type]:
                batch_started = time.monotonic()
                batch_limit = min(settings.batch_size, selected_totals[media_type] - offset)
                query_started = time.monotonic()
                items = list_items(media_type, limit=batch_limit, offset=offset)
                catalog_query_seconds = time.monotonic() - query_started
                if not items:
                    break
                batch_number += 1
                tracking_started = time.monotonic()
                tracked_hashes = _tracked_hashes_for_batch(conn, [item.internal_id for item in items])
                tracking_query_seconds = time.monotonic() - tracking_started
                path_started = time.monotonic()
                plans: list[_StrmFilePlan] = []
                for item in items:
                    try:
                        output_path = _movie_output_path(item, settings) if item.media_type == "movie" else _episode_output_path(item, settings)
                        root = settings.movies_output_directory if item.media_type == "movie" else settings.series_output_directory
                        _ensure_output_path_inside_root(output_path, root)
                        try:
                            conn.execute("INSERT INTO strm_desired_paths VALUES (?, ?)", (str(output_path), item.internal_id))
                        except sqlite3.IntegrityError:
                            output_path = output_path.with_name(f"{output_path.stem} [{item.internal_id}]{output_path.suffix}")
                            _ensure_output_path_inside_root(output_path, root)
                            conn.execute("INSERT INTO strm_desired_paths VALUES (?, ?)", (str(output_path), item.internal_id))
                        conn.execute("INSERT OR IGNORE INTO strm_selected_ids VALUES (?)", (item.internal_id,))
                        runtime_url = _runtime_url(item, runtime_base_url)
                        content = f"{runtime_url}\n"
                        digest = _content_hash(content)
                        tracked = tracked_hashes.get((item.internal_id, str(output_path)))
                        plans.append(_StrmFilePlan(
                            item=item, output_path=output_path, runtime_url=runtime_url, content=content,
                            content_bytes=content.encode("utf-8"), content_hash=digest,
                            tracking_current=bool(tracked and tracked[0] == digest),
                            tracked_at_timestamp=tracked[1] if tracked else None,
                        ))
                    except Exception as exc:
                        counts["fail"] = counts.get("fail", 0) + 1
                        failed_path = output_path if "output_path" in locals() else Path("")
                        if len(operations) < MAX_OPERATION_PREVIEW:
                            operations.append(_operation("fail", item, failed_path, None, f"Failed to prepare STRM output: {exc}"))
                        UVICORN_LOGGER.exception("STRM %s failed for catalog item %s at %s", mode, item.internal_id, failed_path)
                path_construction_seconds = time.monotonic() - path_started
                directory_started = time.monotonic()
                if not effective_dry_run:
                    for directory in dict.fromkeys(plan.output_path.parent for plan in plans):
                        directory.mkdir(parents=True, exist_ok=True)
                directory_creation_seconds = time.monotonic() - directory_started

                file_stage_started = time.monotonic()
                results = list(executor.map(
                    lambda plan: _process_strm_file(plan, dry_run=effective_dry_run,
                                                    overwrite=settings.overwrite_existing_files),
                    plans,
                ))
                file_stage_seconds = time.monotonic() - file_stage_started
                file_check_seconds = sum(result.check_seconds for result in results)
                file_write_seconds = sum(result.write_seconds for result in results)
                for result in results:
                    counts[result.action] = counts.get(result.action, 0) + 1
                    if len(operations) < MAX_OPERATION_PREVIEW:
                        operations.append(_operation(result.action, result.plan.item, result.plan.output_path,
                                                     result.plan.runtime_url if result.error is None else None, result.reason))
                    if result.error is not None:
                        UVICORN_LOGGER.error("STRM %s failed for catalog item %s at %s", mode,
                                             result.plan.item.internal_id, result.plan.output_path,
                                             exc_info=(type(result.error), result.error, result.error.__traceback__))

                sqlite_started = time.monotonic()
                if not effective_dry_run:
                    _record_generated_files(conn, results, datetime.utcnow().isoformat(), run_id)
                sqlite_update_seconds = time.monotonic() - sqlite_started
                commit_started = time.monotonic()
                if not effective_dry_run:
                    conn.commit()
                commit_seconds = time.monotonic() - commit_started
                counts[media_type] += len(items)
                offset += len(items)
                processed = counts["movie"] + counts["episode"]
                progress = 100 if total_items == 0 else min(99, int(processed * 100 / total_items))
                progress_result = {**_summary_from_counts(mode, counts, output_paths, time.monotonic() - started, settings, total_items, excluded).model_dump(), "current_media_type": media_type, "current_batch": batch_number, "percentage_complete": progress}
                progress_started = time.monotonic()
                if job_id:
                    update_job(job_id, progress=progress, message=f"{media_type.title()} batch {batch_number}: {processed}/{total_items}", result=progress_result)
                progress_update_seconds = time.monotonic() - progress_started
                batch_seconds = time.monotonic() - batch_started
                batch_rate = len(items) / batch_seconds if batch_seconds else 0
                add_log("info", "outputs", (
                    f"STRM {mode} batch {batch_number}: media_type={media_type}, items={len(items)}, "
                    f"processed={processed}/{total_items}, created={counts.get('create', 0)}, updated={counts.get('update', 0)}, "
                    f"skipped={counts.get('skip', 0)}, failed={counts.get('fail', 0)}; timing_s "
                    f"catalog_query={catalog_query_seconds:.3f}, path_construction={path_construction_seconds:.3f}, "
                    f"directory_creation={directory_creation_seconds:.3f}, file_checks_worker={file_check_seconds:.3f}, "
                    f"file_writes_worker={file_write_seconds:.3f}, file_stage_wall={file_stage_seconds:.3f}, "
                    f"tracking_query={tracking_query_seconds:.3f}, sqlite_updates={sqlite_update_seconds:.3f}, "
                    f"sqlite_commit={commit_seconds:.3f}, progress_update={progress_update_seconds:.3f}, "
                    f"batch_duration={batch_seconds:.3f}, items_per_second={batch_rate:.2f}"
                ))
                del items, plans, results, tracked_hashes
                if job_id and is_job_cancel_requested(job_id):
                    cancelled = True
                    break
            if cancelled:
                break

        if settings.remove_orphaned_files and not cancelled:
            orphan_rows = conn.execute("""
                SELECT f.* FROM output_generated_files f
                LEFT JOIN catalog_items c ON c.internal_id=f.catalog_item_id
                LEFT JOIN strm_selected_ids s ON s.catalog_item_id=f.catalog_item_id
                LEFT JOIN strm_desired_paths d ON d.output_path=f.output_path
                WHERE f.output_type=? AND f.status='generated'
                  AND (c.internal_id IS NULL OR (s.catalog_item_id IS NOT NULL AND d.output_path IS NULL))
                ORDER BY f.id LIMIT ?
            """, (OUTPUT_TYPE, MAX_OUTPUT_ROWS)).fetchall()
            for row in orphan_rows:
                tracked_path = Path(row["output_path"])
                reason = "Tracked generated file is no longer produced by current catalog/settings."
                if not effective_dry_run and tracked_path.exists():
                    try:
                        tracked_path.unlink()
                    except OSError as exc:
                        counts["fail"] = counts.get("fail", 0) + 1
                        if len(operations) < MAX_OPERATION_PREVIEW: operations.append(
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
                if len(operations) < MAX_OPERATION_PREVIEW: operations.append(
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

        summary = _summary_from_counts(mode, counts, output_paths, time.monotonic() - started, settings, total_items, excluded).model_copy(update={
            "percentage_complete": 100 if not cancelled else (100 if total_items == 0 else int((counts["movie"] + counts["episode"]) * 100 / total_items)),
            "current_media_type": "cancelled" if cancelled else "complete",
            "current_batch": batch_number,
        })
        status = "cancelled" if cancelled else ("complete" if summary.failed_count == 0 else "failed")
        finished_at = datetime.utcnow().isoformat()
        if not effective_dry_run:
            conn.commit()
        _record_run(conn, mode, status, summary, started_at, finished_at)
        conn.commit()

    add_log(
        "info" if summary.failed_count == 0 else "error",
        "outputs",
        (
            f"STRM {mode} {'cancelled' if cancelled else 'completed'}: {summary.created_count} created, {summary.updated_count} updated, "
            f"{summary.skipped_count} skipped, {summary.removed_count} removed, {summary.failed_count} failed; "
            f"total_elapsed={summary.duration_seconds:.3f}s, items_per_second={summary.items_per_second:.2f}, "
            f"average_ms_per_item={summary.average_ms_per_item:.3f}"
        ),
    )
    return StrmOutputResult(settings=settings, runtime_base_url=runtime_base_url, summary=summary, operations=operations)


def dry_run_strm_outputs(request_base_url: str | None = None) -> StrmOutputResult:
    return build_strm_outputs(request_base_url=request_base_url, dry_run=True)


def generate_strm_outputs(request_base_url: str | None = None) -> StrmOutputResult:
    return build_strm_outputs(request_base_url=request_base_url, dry_run=False)


def build_live_m3u_output(request_base_url: str | None = None, dry_run: bool = True, job_id: str | None = None) -> LiveM3uOutputResult:
    started = time.monotonic()
    started_at = datetime.utcnow().isoformat()
    settings = get_live_m3u_settings()
    _validate_live_m3u_settings(settings)
    effective_dry_run = dry_run or settings.dry_run_mode
    mode = "dry_run" if effective_dry_run else "generate"
    runtime_base_url = _live_runtime_base(settings, request_base_url)
    output_path = Path(settings.output_file_path)
    estimate = get_live_m3u_estimate()
    configured_limit = estimate.configured_limit
    catalog_total = estimate.total_live_channels
    eligible_total = estimate.eligible_live_channels
    included_target = estimate.included_channels
    excluded_by_limit = estimate.excluded_by_limit
    unavailable_count = catalog_total - eligible_total
    add_log("info", "outputs", f"Live M3U {mode} started: generation_mode={settings.generation_mode}, configured_channel_limit={configured_limit}, eligible_channels={eligible_total}")
    if not effective_dry_run:
        validation = validate_live_m3u_paths(create_missing=True)
        failed = [item for item in validation.paths if item.status in {"error", "invalid"}]
        if failed:
            detail = "; ".join(f"{item.purpose} {item.path}: {item.message}" for item in failed)
            raise OutputPathError(f"Live M3U output path validation failed: {detail}")

    created_count = updated_count = failed_count = 0
    preview_entries: list[LiveM3uPreviewEntry] = []
    skipped_channels: list[str] = []
    first_catalog_item_id: str | None = None
    digest = ""
    output_existed = output_path.exists()
    with _connect() as work_conn:
        work_conn.execute("""CREATE TEMP TABLE live_m3u_entries (
            placement_id INTEGER PRIMARY KEY, catalog_item_id TEXT, title TEXT, channel_number TEXT, group_title TEXT,
            extinf TEXT NOT NULL, runtime_url TEXT NOT NULL, sort_missing INTEGER NOT NULL,
            sort_number REAL NOT NULL, sort_channel TEXT NOT NULL, editorial_rank INTEGER NOT NULL,
            source_name TEXT NOT NULL, placement_index INTEGER NOT NULL)""")
        offset = included = 0
        batch_size = 250
        while included < included_target:
            availability_filter = "" if settings.include_disabled_channels else """ AND EXISTS (
                SELECT 1 FROM source_availability s WHERE s.catalog_internal_id=p.catalog_item_id
                AND s.media_type='channel' AND s.enabled=1)"""
            placements = work_conn.execute(f"""SELECT p.* FROM channel_placements p
                WHERE p.active=1 AND (p.source_identity != 'legacy-canonical' OR NOT EXISTS (
                    SELECT 1 FROM channel_placements real WHERE real.catalog_item_id=p.catalog_item_id
                    AND real.active=1 AND real.source_identity != 'legacy-canonical'))
                {availability_filter}
                ORDER BY CASE WHEN p.source_identity='legacy-canonical' THEN 1 ELSE 0 END,
                    LOWER(p.source_name), p.source_identity, p.placement_index
                LIMIT ? OFFSET ?""", (min(batch_size, included_target-included), offset)).fetchall()
            if not placements:
                break
            offset += len(placements)
            for placement in placements:
                entry = _live_m3u_placement_entry(placement, settings, runtime_base_url)
                sort_missing, sort_number, sort_channel = _channel_number_sort_key(entry.channel_number)
                work_conn.execute("INSERT INTO live_m3u_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                    placement["placement_id"], entry.catalog_item_id, entry.title, entry.channel_number, entry.group_title,
                    entry.extinf, entry.runtime_url, sort_missing, sort_number, sort_channel,
                    1 if placement["source_identity"] == "legacy-canonical" else 0,
                    placement["source_name"], placement["placement_index"],
                ))
                included += 1
                if included >= included_target:
                    break
            if job_id:
                progress = 90 if included_target == 0 else min(90, int(included * 90 / included_target))
                update_job(job_id, progress=progress, message=f"Live channels: {included}/{included_target}", result={
                    "total_live_channels": catalog_total, "eligible_live_channels": eligible_total,
                    "configured_limit": configured_limit, "included_channels": included,
                    "excluded_by_limit": excluded_by_limit, "skipped_count": unavailable_count,
                    "written_count": included, "generation_mode": settings.generation_mode,
                    "capped": settings.generation_mode != "Unlimited", "percentage_complete": progress,
                    "duration_seconds": round(time.monotonic() - started, 3), "created_count": 0,
                    "updated_count": 0, "removed_count": 0, "failed_count": 0,
                })
            del placements

        ordered = work_conn.execute("""SELECT * FROM live_m3u_entries ORDER BY
            editorial_rank, CASE WHEN editorial_rank=0 THEN LOWER(source_name) ELSE '' END,
            CASE WHEN editorial_rank=0 THEN placement_index ELSE 0 END,
            sort_missing, sort_number, sort_channel, LOWER(COALESCE(group_title,'')),
            LOWER(title), placement_id""")
        hasher = hashlib.sha256()
        header = "#EXTM3U\n"
        hasher.update(header.encode("utf-8"))
        temp_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
        handle = None
        try:
            if not effective_dry_run:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                handle = temp_path.open("w")
                handle.write(header)
            for row in ordered:
                text = f"{row['extinf']}\n{row['runtime_url']}\n"
                hasher.update(text.encode("utf-8"))
                if handle:
                    handle.write(text)
                if first_catalog_item_id is None:
                    first_catalog_item_id = row["catalog_item_id"]
                if len(preview_entries) < 10:
                    preview_entries.append(LiveM3uPreviewEntry(
                        catalog_item_id=row["catalog_item_id"], title=row["title"],
                        channel_number=row["channel_number"], group_title=row["group_title"],
                        extinf=row["extinf"], runtime_url=row["runtime_url"],
                    ))
            if handle:
                handle.close()
                handle = None
            digest = hasher.hexdigest()
            existing_digest = None
            if output_path.exists():
                existing_hasher = hashlib.sha256()
                with output_path.open("rb") as existing:
                    for chunk in iter(lambda: existing.read(1024 * 1024), b""):
                        existing_hasher.update(chunk)
                existing_digest = existing_hasher.hexdigest()
            if not effective_dry_run and existing_digest != digest:
                temp_path.replace(output_path)
                created_count = 1 if not output_existed else 0
                updated_count = 0 if not output_existed else 1
            elif temp_path.exists():
                temp_path.unlink()
        except OSError as exc:
            failed_count = 1
            if handle:
                handle.close()
            if temp_path.exists():
                temp_path.unlink()
            UVICORN_LOGGER.exception("Live M3U %s failed at %s", mode, output_path)
            if not effective_dry_run:
                raise OutputPathError(f"Could not write Live M3U file at {output_path}: {exc}") from exc

    summary = LiveM3uOutputSummary(
        mode=mode,
        total_live_channels=catalog_total,
        written_count=included_target,
        skipped_count=unavailable_count,
        created_count=created_count,
        updated_count=updated_count,
        failed_count=failed_count,
        output_path=str(output_path),
        duration_seconds=round(time.monotonic() - started, 3),
        eligible_live_channels=eligible_total,
        configured_limit=configured_limit,
        included_channels=included_target,
        excluded_by_limit=excluded_by_limit,
        capped=settings.generation_mode != "Unlimited",
        generation_mode=settings.generation_mode,
        percentage_complete=100,
    )
    if effective_dry_run and output_existed and existing_digest == digest:
        summary.created_count = 0
        summary.updated_count = 0
    elif effective_dry_run:
        summary.created_count = 1 if not output_existed else 0
        summary.updated_count = 0 if not output_existed else 1

    status = "complete" if summary.failed_count == 0 else "failed"
    finished_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        _record_live_m3u_run(conn, mode, status, summary, started_at, finished_at)
        if not effective_dry_run and summary.failed_count == 0 and first_catalog_item_id:
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
                (LIVE_M3U_OUTPUT_ID, first_catalog_item_id, LIVE_M3U_OUTPUT_TYPE, str(output_path), digest, datetime.utcnow().isoformat()),
            )
        conn.commit()
    add_log(
        "info" if summary.failed_count == 0 else "error",
        "outputs",
        (
            f"Live M3U {mode} completed: generation_mode={settings.generation_mode}, eligible={eligible_total}, "
            f"{summary.written_count} channels written, {excluded_by_limit} excluded_by_limit, "
            f"{summary.skipped_count} skipped, {summary.created_count} created, "
            f"{summary.updated_count} updated, {summary.failed_count} failed"
        ),
    )
    return LiveM3uOutputResult(
        settings=settings,
        runtime_base_url=runtime_base_url,
        summary=summary,
        preview_entries=preview_entries,
        skipped_channels=skipped_channels,
    )


def dry_run_live_m3u_output(request_base_url: str | None = None) -> LiveM3uOutputResult:
    return build_live_m3u_output(request_base_url=request_base_url, dry_run=True)


def generate_live_m3u_output(request_base_url: str | None = None) -> LiveM3uOutputResult:
    return build_live_m3u_output(request_base_url=request_base_url, dry_run=False)


def run_strm_generate_job(job_id: str, request_base_url: str | None = None) -> None:
    update_job(job_id, status="running", progress=10, message="Preparing STRM output generation")
    add_log("info", "outputs", f"STRM generate job {job_id} started")
    try:
        result = build_strm_outputs(request_base_url, dry_run=False, job_id=job_id)
        failed_operations = [operation for operation in result.operations if operation.action == "fail"]
        failure_reason = failed_operations[0].reason if failed_operations else ""
        cancelled = is_job_cancel_requested(job_id)
        status = "cancelled" if cancelled else ("complete" if result.summary.failed_count == 0 else "failed")
        message = (
            f"STRM generation {'cancelled' if cancelled else 'complete'}: {result.summary.created_count} created, "
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
        add_log("info" if status in {"complete", "cancelled"} else "error", "outputs", message)
    except Exception as exc:
        UVICORN_LOGGER.exception("STRM generate job %s failed", job_id)
        update_job(job_id, status="failed", progress=100, message=f"STRM generation failed: {exc}", result={"failed_count": 1, "failure_reason": str(exc)})
        add_log("error", "outputs", f"STRM generation failed: {exc}")
    finally:
        clear_job_cancel_request(job_id)


def run_live_m3u_generate_job(job_id: str, request_base_url: str | None = None) -> None:
    update_job(job_id, status="running", progress=10, message="Preparing Live M3U output generation")
    add_log("info", "outputs", f"Live M3U generate job {job_id} started")
    try:
        result = build_live_m3u_output(request_base_url, dry_run=False, job_id=job_id)
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


def list_generated_files(limit: int = 100, offset: int = 0) -> list[GeneratedOutputFile]:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    with _connect() as conn:
        rows = conn.execute("""SELECT * FROM output_generated_files WHERE output_type = ?
            ORDER BY last_generated_at DESC, output_path LIMIT ? OFFSET ?""", (OUTPUT_TYPE, limit, offset)).fetchall()
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
