from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
import json
import os
import re
import sqlite3
import threading
from typing import Any
from urllib.parse import unquote

from app.core.config import get_settings
from app.core.json_store import JsonStore
from app.services.catalog import import_paths, run_catalog_import_job
from app.services.jobs import create_job, get_job, has_active_job, update_job
from app.services.logs import add_log
from app.core.redaction import redact_text


DAILY = timedelta(days=1)
WEEKLY = timedelta(days=7)
CHECK_INTERVAL_SECONDS = 60
FAILED_RETRY_DELAY = timedelta(hours=1)
_store_lock = threading.Lock()


def _store() -> JsonStore:
    return JsonStore(get_settings().data_dir / "playlist_refresh_sources.json", {"sources": []})


def _source_id(paths: list[str], account_id: str | None, media_type: str | None) -> str:
    payload = json.dumps([paths, account_id, media_type or ""], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _interval(media_type: str | None) -> timedelta | None:
    if media_type in {"live", "channel"}:
        return DAILY
    if media_type in {"movie", "series", "episode", "vod"}:
        return WEEKLY
    return None


def register_refresh_source(
    paths: list[str], source_name: str, provider_id: str | None,
    account_id: str | None, media_type: str | None,
    source_feed_id: str | None, *, last_success_at: str | None = None,
) -> None:
    """Remember a repeatable import. Only explicitly scoped imports are scheduled."""
    if _interval(media_type) is None:
        return
    paths = list(dict.fromkeys(paths))
    source_id = _source_id(paths, account_id, media_type)
    now = datetime.utcnow().isoformat()
    record = {
        "id": source_id, "paths": paths, "source_name": source_name,
        "provider_id": provider_id, "account_id": account_id,
        "media_type": media_type, "source_feed_id": source_feed_id,
        "last_success_at": last_success_at or now, "last_attempt_at": None,
    }
    with _store_lock:
        data = _store().read()
        existing = next((row for row in data["sources"] if row.get("id") == source_id), None)
        if existing:
            # A manual import refreshes both the reusable request and its due date.
            existing.update(record)
        else:
            data["sources"].append(record)
        store = _store()
        store.write(data)
        os.chmod(store.path, 0o600)


def _legacy_media_type(summary_json: str) -> str | None:
    try:
        summary = json.loads(summary_json)
    except (TypeError, json.JSONDecodeError):
        return None
    channels = int(summary.get("channels", 0) or 0)
    vod = sum(int(summary.get(key, 0) or 0) for key in ("movies", "series", "episodes"))
    if channels and not vod:
        return "live"
    if vod and not channels:
        return "vod"
    return None


def _restore_legacy_path(path: str) -> str:
    # Older catalog_import rows passed URLs through pathlib, collapsing `://`.
    return re.sub(r"^(https?):/(?!/)", r"\1://", path)


def _legacy_vod_media_type(path: str) -> str | None:
    decoded = unquote(path)
    if re.search(r"(?:^|[/ _-])movies?(?:[/. _-]|$)", decoded, re.IGNORECASE):
        return "movie"
    if re.search(
        r"(?:^|[/ _-])(series|episodes?|tv[ _-]?shows?)(?:[/. _-]|$)",
        decoded, re.IGNORECASE,
    ):
        return "series"
    return None


def _normalize_legacy_registry() -> None:
    with _store_lock:
        data = _store().read()
        changed = False
        for row in data["sources"]:
            if row.get("media_type") != "vod":
                continue
            inferred = next(
                (_legacy_vod_media_type(path) for path in row.get("paths", [])
                 if _legacy_vod_media_type(path)),
                None,
            )
            if inferred:
                row["media_type"] = inferred
                changed = True
        if changed:
            store = _store()
            store.write(data)
            os.chmod(store.path, 0o600)


def register_existing_imports() -> int:
    """Adopt legacy imports so an upgrade schedules already configured playlists."""
    _normalize_legacy_registry()
    db_path = get_settings().data_dir / "media_router.db"
    if not db_path.exists():
        return 0
    adopted = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT current.file_path,current.source_name,current.summary_json,current.imported_at
               FROM catalog_imports AS current
               WHERE current.status='completed' AND current.id=(
                   SELECT latest.id FROM catalog_imports AS latest
                   WHERE latest.status='completed'
                     AND latest.file_path=current.file_path
                     AND latest.source_name=current.source_name
                   ORDER BY latest.imported_at DESC,latest.id DESC LIMIT 1
               ) ORDER BY current.imported_at"""
        ).fetchall()
        accounts = conn.execute(
            """SELECT accounts.id AS account_id,accounts.provider_id,
                      providers.friendly_name || ' / ' || accounts.friendly_name AS source_name
               FROM accounts JOIN providers ON providers.id=accounts.provider_id"""
        ).fetchall()
        live_rows = conn.execute(
            """SELECT source_playlist AS file_path,source_name,MAX(updated_at) AS imported_at
               FROM channel_placements GROUP BY source_playlist,source_name"""
        ).fetchall()
    account_by_name = {row["source_name"]: row for row in accounts}
    configured = {
        (path, row.get("source_name"))
        for row in _store().read()["sources"] for path in row.get("paths", [])
    }
    candidates = [
        (row["file_path"], row["source_name"], "live", row["imported_at"])
        for row in live_rows
    ]
    live_paths = {_restore_legacy_path(row["file_path"]) for row in live_rows}
    for row in rows:
        restored_path = _restore_legacy_path(row["file_path"])
        media_type = _legacy_media_type(row["summary_json"])
        if media_type in {None, "vod"} and restored_path not in live_paths:
            media_type = _legacy_vod_media_type(restored_path)
        if media_type:
            candidates.append((restored_path, row["source_name"], media_type, row["imported_at"]))

    for file_path, source_name, media_type, imported_at in candidates:
        legacy_key = (file_path, source_name)
        if legacy_key in configured:
            continue
        account = account_by_name.get(source_name)
        before = len(_store().read()["sources"])
        register_refresh_source(
            [file_path], source_name,
            account["provider_id"] if account else None,
            account["account_id"] if account else None,
            media_type, None, last_success_at=imported_at,
        )
        was_added = len(_store().read()["sources"]) > before
        adopted += int(was_added)
        if was_added:
            configured.add(legacy_key)
    return adopted


def _due(record: dict[str, Any], now: datetime) -> bool:
    interval = _interval(record.get("media_type"))
    if interval is None:
        return False
    try:
        last_success = datetime.fromisoformat(record["last_success_at"])
    except (KeyError, TypeError, ValueError):
        return True
    if now < last_success + interval:
        return False
    try:
        last_attempt = datetime.fromisoformat(record["last_attempt_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return now >= last_attempt + FAILED_RETRY_DELAY


def _update_record(source_id: str, **values: Any) -> None:
    with _store_lock:
        data = _store().read()
        for row in data["sources"]:
            if row.get("id") == source_id:
                row.update(values)
                break
        store = _store()
        store.write(data)
        os.chmod(store.path, 0o600)


def run_library_refresh_job(job_id: str) -> None:
    """Force one sequential re-import of every configured playlist source."""
    records = list(_store().read()["sources"])
    if not records:
        update_job(
            job_id, status="complete", progress=100,
            message="Library refresh complete: no scheduled playlist sources",
            result={"sources": 0, "completed": 0, "failed": 0},
        )
        return
    completed = 0
    failures: list[str] = []
    update_job(job_id, status="running", progress=1, message="Starting library refresh")
    for index, record in enumerate(records):
        _update_record(record["id"], last_attempt_at=datetime.utcnow().isoformat())
        try:
            import_paths(
                record["paths"], record["source_name"], None,
                record.get("provider_id"), record.get("account_id"),
                record.get("media_type"), record.get("source_feed_id"),
            )
            _update_record(record["id"], last_success_at=datetime.utcnow().isoformat())
            completed += 1
        except Exception as exc:
            failures.append(redact_text(exc))
        progress = int(((index + 1) / len(records)) * 100)
        update_job(
            job_id, status="running", progress=progress,
            message=f"Refreshing library sources: {index + 1}/{len(records)}",
        )
    failed = len(failures)
    status = "failed" if failed else "complete"
    message = f"Library refresh complete: {completed} succeeded, {failed} failed"
    if failures:
        message += f"; first error: {failures[0]}"
    update_job(
        job_id, status=status, progress=100, message=message,
        result={"sources": len(records), "completed": completed, "failed": failed},
    )


class PlaylistRefreshScheduler:
    def __init__(self, check_interval_seconds: int = CHECK_INTERVAL_SECONDS) -> None:
        self.check_interval_seconds = check_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        adopted = await asyncio.to_thread(register_existing_imports)
        if adopted:
            add_log("info", "catalog", f"Scheduled {adopted} existing playlist source(s) for refresh")
        self._task = asyncio.create_task(self._run(), name="playlist-refresh-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    async def run_due_once(self) -> int:
        if has_active_job("catalog_import") or has_active_job("library_refresh"):
            return 0
        now = datetime.utcnow()
        records = list(_store().read()["sources"])
        completed = 0
        for record in records:
            if not _due(record, now) or has_active_job("catalog_import"):
                continue
            _update_record(record["id"], last_attempt_at=now.isoformat())
            job = create_job("catalog_import")
            await asyncio.to_thread(
                run_catalog_import_job, job.id, record["paths"], record["source_name"],
                record.get("provider_id"), record.get("account_id"),
                record.get("media_type"), record.get("source_feed_id"),
            )
            result = get_job(job.id)
            if result and result.status == "complete":
                _update_record(record["id"], last_success_at=datetime.utcnow().isoformat())
                completed += 1
        return completed

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_due_once()
            except Exception as exc:
                add_log("error", "catalog", f"Playlist refresh scheduler failed: {redact_text(exc)}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.check_interval_seconds)
            except TimeoutError:
                pass
