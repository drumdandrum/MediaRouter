from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import os
import re
import sqlite3
import time
from typing import Any
from urllib.request import urlopen

from app.core.config import get_settings
from app.schemas.catalog import CatalogItem, CatalogSource, CatalogSummary, SourceAvailability, SourceAvailabilityUpdate
from app.services.jobs import update_job
from app.services.logs import add_log
from app.services.providers import ensure_provider_schema, get_account


EXTINF_RE = re.compile(r'^#EXTINF:[^,]*?(?P<attrs>(?:\s+[A-Za-z0-9_-]+="[^"]*")*)\s*,(?P<title>.*)$')
ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
EPISODE_RE = re.compile(r"(?P<show>.+?)[\s._-]+S(?P<season>\d{1,2})E(?P<episode>\d{1,3})(?:[\s._-]+(?P<title>.+))?$", re.IGNORECASE)
PREFIX_RE = re.compile(r"^[A-Z]{2,4}\s*-\s*")
MEDIA_TYPE_HINTS = {None, "", "live", "channel", "movie", "series", "episode"}


@dataclass
class ParsedEntry:
    title: str
    attrs: dict[str, str]
    raw_extinf: str
    url: str
    media_type: str
    show_name: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    episode_title: str | None = None
    confidence: str = "high"


def _db_path() -> Path:
    return get_settings().data_dir / "media_router.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    if conn is None:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    ensure_provider_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS catalog_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            internal_id TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL,
            title TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            group_title TEXT,
            tvg_id TEXT,
            tvg_name TEXT,
            tvg_logo TEXT,
            tvg_chno TEXT,
            cuid TEXT,
            show_name TEXT,
            season_number INTEGER,
            episode_number INTEGER,
            episode_title TEXT,
            parent_internal_id TEXT,
            confidence TEXT NOT NULL DEFAULT 'high',
            raw_title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS catalog_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_internal_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            cuid TEXT,
            tvg_id TEXT,
            raw_extinf TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE(catalog_internal_id, source_url),
            FOREIGN KEY(catalog_internal_id) REFERENCES catalog_items(internal_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS catalog_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            source_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_internal_id TEXT NOT NULL,
            provider_id TEXT,
            account_id TEXT,
            external_id TEXT,
            location_ref TEXT NOT NULL,
            media_type TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT NOT NULL,
            metadata_confidence TEXT NOT NULL DEFAULT 'high',
            notes TEXT NOT NULL DEFAULT '',
            raw_extinf TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(catalog_internal_id) REFERENCES catalog_items(internal_id) ON DELETE CASCADE,
            FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE SET NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_catalog_items_type ON catalog_items(media_type);
        CREATE INDEX IF NOT EXISTS idx_catalog_items_parent ON catalog_items(parent_internal_id);
        CREATE INDEX IF NOT EXISTS idx_catalog_sources_item ON catalog_sources(catalog_internal_id);
        CREATE INDEX IF NOT EXISTS idx_source_availability_item ON source_availability(catalog_internal_id);
        CREATE INDEX IF NOT EXISTS idx_source_availability_account ON source_availability(account_id);
        """
    )
    columns = {row["name"] if hasattr(row, "keys") else row[1] for row in conn.execute("PRAGMA table_info(catalog_items)").fetchall()}
    if "tvg_chno" not in columns:
        conn.execute("ALTER TABLE catalog_items ADD COLUMN tvg_chno TEXT")
    conn.execute(
        """
        INSERT INTO source_availability (
            catalog_internal_id, provider_id, account_id, external_id, location_ref, media_type,
            enabled, last_seen_at, metadata_confidence, notes, raw_extinf, created_at, updated_at
        )
        SELECT
            catalog_sources.catalog_internal_id,
            NULL,
            NULL,
            COALESCE(NULLIF(catalog_sources.cuid, ''), NULLIF(catalog_sources.tvg_id, '')),
            catalog_sources.source_url,
            catalog_sources.media_type,
            1,
            catalog_sources.last_seen_at,
            'high',
            'Migrated from Sprint 2 source mapping',
            catalog_sources.raw_extinf,
            catalog_sources.first_seen_at,
            catalog_sources.last_seen_at
        FROM catalog_sources
        WHERE NOT EXISTS (
            SELECT 1 FROM source_availability
            WHERE source_availability.catalog_internal_id = catalog_sources.catalog_internal_id
              AND COALESCE(source_availability.account_id, '') = ''
              AND source_availability.location_ref = catalog_sources.source_url
        )
        """
    )
    conn.commit()
    if owns_conn:
        conn.close()


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def stable_id(prefix: str, key: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{normalize(key)}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _media_type_from_context(source_label: str, url: str, attrs: dict[str, str], title: str, media_type_hint: str | None = None) -> str:
    if media_type_hint in {"live", "channel"}:
        return "live"
    if media_type_hint == "movie":
        return "movie"
    if media_type_hint in {"series", "episode"}:
        return "episode"
    probe = f"{source_label} {url} {attrs.get('group-title', '')} {title}".lower()
    if "series" in probe or "/series/" in probe:
        return "episode"
    if "movie" in probe or "movies" in probe or "/movie/" in probe:
        return "movie"
    return "live"


def _parse_series(title: str) -> tuple[str | None, int | None, int | None, str | None, str]:
    cleaned = PREFIX_RE.sub("", title).strip()
    match = EPISODE_RE.match(cleaned)
    if not match:
        return None, None, None, None, "low"
    show = re.sub(r"[-_.]+", " ", match.group("show")).strip()
    episode_title = match.group("title")
    if episode_title:
        episode_title = re.sub(r"[-_.]+", " ", episode_title).strip()
    return show, int(match.group("season")), int(match.group("episode")), episode_title, "high"


def _iter_playlist_lines(raw_source: str):
    if raw_source.startswith(("http://", "https://")):
        with urlopen(raw_source, timeout=30) as response:
            for raw_line in response:
                yield raw_line.decode("utf-8", errors="ignore")
        return
    with Path(raw_source).open("r", errors="ignore") as handle:
        for line in handle:
            yield line


def normalize_playlist_sources(paths: list[str] | None = None, playlist: str | None = None) -> list[str]:
    sources: list[str] = []
    if playlist and playlist.strip():
        sources.append(playlist.strip())
    for path in paths or []:
        if path and path.strip():
            sources.append(path.strip())
    return list(dict.fromkeys(sources))


def validate_playlist_sources(paths: list[str]) -> None:
    if not paths:
        raise ValueError("Enter a playlist file, container path, or HTTP/HTTPS URL before importing.")
    for raw_path in paths:
        if raw_path.startswith(("http://", "https://")):
            continue
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Playlist path is not visible inside the container: {raw_path}")
        if not path.is_file():
            raise ValueError(f"Playlist path is not a file: {raw_path}")
        if not os.access(path, os.R_OK):
            raise PermissionError(f"Playlist file is not readable: {raw_path}")


def validate_media_type_hint(media_type_hint: str | None) -> None:
    if media_type_hint not in MEDIA_TYPE_HINTS:
        raise ValueError("Unsupported media type. Use Auto detect, Live Channels, Movies, or Series Episodes.")


def parse_m3u_entries(raw_source: str, media_type_hint: str | None = None):
    current: tuple[dict[str, str], str, str] | None = None
    source_label = raw_source.rsplit("/", 1)[-1]
    for line in _iter_playlist_lines(raw_source):
        stripped = line.strip().rstrip("]")
        if not stripped:
            continue
        if stripped.startswith("#EXTINF"):
            match = EXTINF_RE.match(stripped)
            attrs = {key.lower(): value for key, value in ATTR_RE.findall(match.group("attrs"))} if match else {}
            title = match.group("title").strip() if match else "Untitled"
            current = (attrs, title, stripped)
            continue
        if current and not stripped.startswith("#"):
            attrs, title, raw_extinf = current
            media_type = _media_type_from_context(source_label, stripped, attrs, title, media_type_hint)
            show, season, episode, episode_title, confidence = (None, None, None, None, "high")
            if media_type == "episode":
                show, season, episode, episode_title, confidence = _parse_series(attrs.get("tvg-name") or title)
            yield ParsedEntry(
                title=title,
                attrs=attrs,
                raw_extinf=raw_extinf,
                url=stripped,
                media_type=media_type,
                show_name=show,
                season_number=season,
                episode_number=episode,
                episode_title=episode_title,
                confidence=confidence,
            )
            current = None


def parse_m3u(path: Path, source_name: str) -> list[ParsedEntry]:
    return list(parse_m3u_entries(str(path)))


def _item_key(entry: ParsedEntry) -> tuple[str, str, str]:
    cuid = entry.attrs.get("cuid")
    tvg_id = entry.attrs.get("tvg-id")
    tvg_name = entry.attrs.get("tvg-name")
    if entry.media_type == "live":
        key = cuid or tvg_id or tvg_name or entry.title
        return "channel", stable_id("channel", key), entry.title
    if entry.media_type == "movie":
        key = cuid or tvg_id or tvg_name or entry.title
        return "movie", stable_id("movie", key), entry.title
    show = entry.show_name or entry.title
    key = cuid or f"{show}|{entry.season_number}|{entry.episode_number}|{entry.episode_title or entry.title}"
    title = entry.episode_title or entry.title
    return "episode", stable_id("episode", key), title


def _upsert_item(conn: sqlite3.Connection, *, internal_id: str, media_type: str, title: str, entry: ParsedEntry, parent_internal_id: str | None = None) -> str:
    now = datetime.utcnow().isoformat()
    attrs = entry.attrs
    existing = conn.execute("SELECT internal_id FROM catalog_items WHERE internal_id = ?", (internal_id,)).fetchone()
    values = {
        "internal_id": internal_id,
        "media_type": media_type,
        "title": title,
        "normalized_title": normalize(title),
        "group_title": attrs.get("group-title"),
        "tvg_id": attrs.get("tvg-id"),
        "tvg_name": attrs.get("tvg-name"),
        "tvg_logo": attrs.get("tvg-logo"),
        "tvg_chno": attrs.get("tvg-chno") or attrs.get("channel-number") or attrs.get("chno"),
        "cuid": attrs.get("cuid"),
        "show_name": entry.show_name,
        "season_number": entry.season_number,
        "episode_number": entry.episode_number,
        "episode_title": entry.episode_title,
        "parent_internal_id": parent_internal_id,
        "confidence": entry.confidence,
        "raw_title": entry.title,
        "updated_at": now,
    }
    if existing:
        conn.execute(
            """
            UPDATE catalog_items SET
                title=:title, normalized_title=:normalized_title, group_title=:group_title,
                tvg_id=:tvg_id, tvg_name=:tvg_name, tvg_logo=:tvg_logo, tvg_chno=:tvg_chno, cuid=:cuid,
                show_name=:show_name, season_number=:season_number, episode_number=:episode_number,
                episode_title=:episode_title, parent_internal_id=:parent_internal_id,
                confidence=:confidence, raw_title=:raw_title, updated_at=:updated_at
            WHERE internal_id=:internal_id
            """,
            values,
        )
        return "updated"
    else:
        values["created_at"] = now
        conn.execute(
            """
            INSERT INTO catalog_items (
                internal_id, media_type, title, normalized_title, group_title, tvg_id, tvg_name,
                tvg_logo, tvg_chno, cuid, show_name, season_number, episode_number, episode_title,
                parent_internal_id, confidence, raw_title, created_at, updated_at
            ) VALUES (
                :internal_id, :media_type, :title, :normalized_title, :group_title, :tvg_id, :tvg_name,
                :tvg_logo, :tvg_chno, :cuid, :show_name, :season_number, :episode_number, :episode_title,
                :parent_internal_id, :confidence, :raw_title, :created_at, :updated_at
            )
            """,
            values,
        )
        return "new"


def _upsert_source(conn: sqlite3.Connection, *, internal_id: str, media_type: str, entry: ParsedEntry, source_name: str) -> None:
    now = datetime.utcnow().isoformat()
    attrs = entry.attrs
    existing = conn.execute(
        "SELECT id FROM catalog_sources WHERE catalog_internal_id = ? AND source_url = ?",
        (internal_id, entry.url),
    ).fetchone()
    if existing:
        conn.execute("UPDATE catalog_sources SET last_seen_at = ?, source_name = ? WHERE id = ?", (now, source_name, existing["id"]))
    else:
        conn.execute(
            """
            INSERT INTO catalog_sources (
                catalog_internal_id, media_type, source_name, source_url, cuid, tvg_id,
                raw_extinf, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (internal_id, media_type, source_name, entry.url, attrs.get("cuid"), attrs.get("tvg-id"), entry.raw_extinf, now, now),
        )


def _upsert_availability(
    conn: sqlite3.Connection,
    *,
    internal_id: str,
    media_type: str,
    entry: ParsedEntry,
    provider_id: str | None,
    account_id: str | None,
) -> str:
    now = datetime.utcnow().isoformat()
    external_id = entry.attrs.get("cuid") or entry.attrs.get("tvg-id") or entry.attrs.get("tvg-name")
    existing = conn.execute(
        """
        SELECT id FROM source_availability
        WHERE catalog_internal_id = ?
          AND COALESCE(account_id, '') = COALESCE(?, '')
          AND location_ref = ?
        """,
        (internal_id, account_id, entry.url),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE source_availability
            SET provider_id = ?, account_id = ?, external_id = ?, media_type = ?, last_seen_at = ?,
                metadata_confidence = ?, raw_extinf = ?, updated_at = ?
            WHERE id = ?
            """,
            (provider_id, account_id, external_id, media_type, now, entry.confidence, entry.raw_extinf, now, existing["id"]),
        )
        return "updated"
    conn.execute(
        """
        INSERT INTO source_availability (
            catalog_internal_id, provider_id, account_id, external_id, location_ref, media_type,
            enabled, last_seen_at, metadata_confidence, notes, raw_extinf, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, '', ?, ?, ?)
        """,
        (internal_id, provider_id, account_id, external_id, entry.url, media_type, now, entry.confidence, entry.raw_extinf, now, now),
    )
    return "new"


def import_paths(
    paths: list[str],
    source_name: str,
    job_id: str | None = None,
    provider_id: str | None = None,
    account_id: str | None = None,
    media_type_hint: str | None = None,
) -> dict[str, Any]:
    ensure_schema()
    paths = list(dict.fromkeys(paths))
    validate_media_type_hint(media_type_hint)
    validate_playlist_sources(paths)
    started = time.monotonic()
    account = get_account(account_id) if account_id else None
    if account_id and account is None:
        raise ValueError("Account not found")
    if account:
        provider_id = account.provider_id
        source_name = f"{account.provider_name or 'Provider'} / {account.friendly_name}"
    summary = {
        "files": 0,
        "entries": 0,
        "new_catalog_items": 0,
        "updated_catalog_items": 0,
        "new_source_availability": 0,
        "updated_source_availability": 0,
        "skipped_records": 0,
        "failed_records": 0,
        "channels": 0,
        "movies": 0,
        "series": 0,
        "episodes": 0,
        "sources": 0,
    }
    with _connect() as conn:
        for index, raw_path in enumerate(paths):
            is_remote = raw_path.startswith(("http://", "https://"))
            path = Path(raw_path)
            if not is_remote and not path.exists():
                raise FileNotFoundError(f"Playlist not found: {raw_path}")
            source_label = raw_path.rsplit("/", 1)[-1]
            summary["files"] += 1
            if job_id:
                update_job(job_id, status="running", progress=min(90, 10 + (index * 80 // max(len(paths), 1))), message=f"Importing {source_label}: 0 processed")
            try:
                for entry in parse_m3u_entries(raw_path, media_type_hint):
                    summary["entries"] += 1
                    item_type, internal_id, title = _item_key(entry)
                    parent_id = None
                    if item_type == "episode":
                        series_title = entry.show_name or title
                        parent_id = stable_id("series", series_title)
                        series_entry = ParsedEntry(
                            title=series_title,
                            attrs=entry.attrs,
                            raw_extinf=entry.raw_extinf,
                            url=entry.url,
                            media_type="series",
                            confidence=entry.confidence,
                        )
                        series_result = _upsert_item(conn, internal_id=parent_id, media_type="series", title=series_title, entry=series_entry)
                        summary["new_catalog_items" if series_result == "new" else "updated_catalog_items"] += 1
                    item_result = _upsert_item(conn, internal_id=internal_id, media_type=item_type, title=title, entry=entry, parent_internal_id=parent_id)
                    source_result = _upsert_availability(conn, internal_id=internal_id, media_type=item_type, entry=entry, provider_id=provider_id, account_id=account_id)
                    summary["new_catalog_items" if item_result == "new" else "updated_catalog_items"] += 1
                    summary["new_source_availability" if source_result == "new" else "updated_source_availability"] += 1
                    if account_id is None:
                        _upsert_source(conn, internal_id=internal_id, media_type=item_type, entry=entry, source_name=source_name)
                    if summary["entries"] % 750 == 0:
                        conn.commit()
                        if job_id:
                            progress = min(95, 10 + (index * 80 // max(len(paths), 1)) + 5)
                            update_job(
                                job_id,
                                status="running",
                                progress=progress,
                                message=(
                                    f"Importing {source_label}: {summary['entries']} processed, "
                                    f"{summary['new_catalog_items']} new items, {summary['updated_catalog_items']} updated items, "
                                    f"{summary['new_source_availability']} new sources, {summary['updated_source_availability']} updated sources, "
                                    f"{summary['skipped_records']} skipped, {summary['failed_records']} failed"
                                ),
                            )
            except Exception:
                summary["failed_records"] += 1
                raise
            imported_at = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO catalog_imports (job_id, source_name, file_path, status, summary_json, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, source_name, str(path), "completed", json.dumps(summary), imported_at),
            )
        conn.commit()
    add_log("info", "catalog", f"Imported {summary['entries']} catalog entries from {summary['files']} playlist file(s)")
    catalog_summary = get_summary().model_dump()
    summary.update(catalog_summary)
    summary["duration_seconds"] = round(time.monotonic() - started, 3)
    if job_id:
        update_job(job_id, result=summary)
    return summary


def run_catalog_import_job(job_id: str, paths: list[str], source_name: str, provider_id: str | None = None, account_id: str | None = None, media_type_hint: str | None = None) -> None:
    try:
        update_job(job_id, status="running", progress=5, message="Starting catalog import")
        summary = import_paths(paths, source_name, job_id, provider_id, account_id, media_type_hint)
        message = (
            f"Catalog import complete in {summary['duration_seconds']}s: {summary['entries']} processed, "
            f"{summary['new_catalog_items']} new items, "
            f"{summary['updated_catalog_items']} updated items, "
            f"{summary['new_source_availability']} new sources, "
            f"{summary['updated_source_availability']} updated sources, "
            f"{summary['skipped_records']} skipped, {summary['failed_records']} failed"
        )
        update_job(job_id, status="complete", progress=100, message=message, result=summary)
    except Exception as exc:
        update_job(job_id, status="failed", progress=100, message=f"Catalog import failed: {exc}")
        add_log("error", "catalog", f"Catalog import failed: {exc}")


def _rows(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(query, params).fetchall()


def _item_from_row(row: sqlite3.Row) -> CatalogItem:
    return CatalogItem(
        internal_id=row["internal_id"],
        media_type=row["media_type"],
        title=row["title"],
        group_title=row["group_title"],
        tvg_id=row["tvg_id"],
        tvg_name=row["tvg_name"],
        tvg_logo=row["tvg_logo"],
        tvg_chno=row["tvg_chno"] if "tvg_chno" in row.keys() else None,
        cuid=row["cuid"],
        show_name=row["show_name"],
        season_number=row["season_number"],
        episode_number=row["episode_number"],
        episode_title=row["episode_title"],
        confidence=row["confidence"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _source_from_row(row: sqlite3.Row) -> CatalogSource:
    return CatalogSource(
        id=row["id"],
        catalog_internal_id=row["catalog_internal_id"],
        media_type=row["media_type"],
        source_name=row["source_name"],
        source_url=_redact_url(row["source_url"]),
        cuid=row["cuid"],
        tvg_id=row["tvg_id"],
        raw_extinf=row["raw_extinf"],
        first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
    )


def _availability_from_row(row: sqlite3.Row) -> SourceAvailability:
    return SourceAvailability(
        id=row["id"],
        catalog_internal_id=row["catalog_internal_id"],
        catalog_title=row["catalog_title"],
        provider_id=row["provider_id"],
        provider_name=row["provider_name"],
        provider_type=row["provider_type"],
        account_id=row["account_id"],
        account_name=row["account_name"],
        priority_group=row["priority_group"],
        weight=row["weight"],
        account_enabled=bool(row["account_enabled"]) if row["account_enabled"] is not None else None,
        account_health_status=row["account_health_status"],
        external_id=row["external_id"],
        location_ref=_redact_url(row["location_ref"]),
        media_type=row["media_type"],
        enabled=bool(row["enabled"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        metadata_confidence=row["metadata_confidence"],
        notes=row["notes"],
    )


def get_summary() -> CatalogSummary:
    ensure_schema()
    with _connect() as conn:
        counts = {
            row["media_type"]: row["count"]
            for row in conn.execute("SELECT media_type, COUNT(*) AS count FROM catalog_items GROUP BY media_type").fetchall()
        }
        sources = conn.execute("SELECT COUNT(*) AS count FROM source_availability").fetchone()["count"]
        last = conn.execute("SELECT MAX(imported_at) AS last_import_time FROM catalog_imports").fetchone()["last_import_time"]
    return CatalogSummary(
        channels=counts.get("channel", 0),
        movies=counts.get("movie", 0),
        series=counts.get("series", 0),
        episodes=counts.get("episode", 0),
        sources=sources,
        last_import_time=datetime.fromisoformat(last) if last else None,
    )


def list_items(media_type: str, limit: int = 100, offset: int = 0) -> list[CatalogItem]:
    ensure_schema()
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    rows = _rows("SELECT * FROM catalog_items WHERE media_type = ? ORDER BY title LIMIT ? OFFSET ?", (media_type, limit, offset))
    return [_item_from_row(row) for row in rows]


def list_all_items(limit: int = 200, offset: int = 0) -> list[CatalogItem]:
    ensure_schema()
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    rows = _rows(
        """
        SELECT *
        FROM catalog_items
        WHERE media_type IN ('channel', 'movie', 'episode')
        ORDER BY media_type, title
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    return [_item_from_row(row) for row in rows]


def get_item(catalog_internal_id: str) -> CatalogItem | None:
    ensure_schema()
    rows = _rows("SELECT * FROM catalog_items WHERE internal_id = ?", (catalog_internal_id,))
    return _item_from_row(rows[0]) if rows else None


def list_sources(limit: int = 100, offset: int = 0) -> list[CatalogSource]:
    ensure_schema()
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    rows = _rows("SELECT * FROM catalog_sources ORDER BY last_seen_at DESC, source_name, id LIMIT ? OFFSET ?", (limit, offset))
    return [_source_from_row(row) for row in rows]


def list_source_availability(catalog_internal_id: str | None = None, limit: int = 100, offset: int = 0) -> list[SourceAvailability]:
    ensure_schema()
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    where = "WHERE source_availability.catalog_internal_id = ?" if catalog_internal_id else ""
    params = ((catalog_internal_id, limit, offset) if catalog_internal_id else (limit, offset))
    rows = _rows(
        f"""
        SELECT
            source_availability.*,
            catalog_items.title AS catalog_title,
            providers.friendly_name AS provider_name,
            providers.provider_type AS provider_type,
            accounts.friendly_name AS account_name,
            accounts.priority_group AS priority_group,
            accounts.weight AS weight,
            accounts.enabled AS account_enabled,
            accounts.health_status AS account_health_status
        FROM source_availability
        LEFT JOIN catalog_items ON catalog_items.internal_id = source_availability.catalog_internal_id
        LEFT JOIN providers ON providers.id = source_availability.provider_id
        LEFT JOIN accounts ON accounts.id = source_availability.account_id
        {where}
        ORDER BY catalog_items.title, providers.friendly_name, accounts.priority_group, accounts.weight DESC
        LIMIT ? OFFSET ?
        """,
        params,
    )
    return [_availability_from_row(row) for row in rows]


def update_source_availability(source_id: int, payload: SourceAvailabilityUpdate) -> SourceAvailability | None:
    ensure_schema()
    data = payload.model_dump(exclude_unset=True)
    if "enabled" in data:
        data["enabled"] = int(data["enabled"])
    if data:
        data["updated_at"] = datetime.utcnow().isoformat()
        assignments = ", ".join(f"{key} = :{key}" for key in data)
        data["id"] = source_id
        with _connect() as conn:
            conn.execute(f"UPDATE source_availability SET {assignments} WHERE id = :id", data)
            conn.commit()
    row = list_source_availability_by_id(source_id)
    return row


def list_source_availability_by_id(source_id: int) -> SourceAvailability | None:
    rows = _rows(
        """
        SELECT
            source_availability.*,
            catalog_items.title AS catalog_title,
            providers.friendly_name AS provider_name,
            providers.provider_type AS provider_type,
            accounts.friendly_name AS account_name,
            accounts.priority_group AS priority_group,
            accounts.weight AS weight,
            accounts.enabled AS account_enabled,
            accounts.health_status AS account_health_status
        FROM source_availability
        LEFT JOIN catalog_items ON catalog_items.internal_id = source_availability.catalog_internal_id
        LEFT JOIN providers ON providers.id = source_availability.provider_id
        LEFT JOIN accounts ON accounts.id = source_availability.account_id
        WHERE source_availability.id = ?
        """,
        (source_id,),
    )
    return _availability_from_row(rows[0]) if rows else None


def delete_source_availability(source_id: int) -> bool:
    ensure_schema()
    with _connect() as conn:
        result = conn.execute("DELETE FROM source_availability WHERE id = ?", (source_id,))
        conn.commit()
    return result.rowcount > 0


def source_availability_summary() -> dict[str, float | int]:
    ensure_schema()
    with _connect() as conn:
        sources = conn.execute("SELECT COUNT(*) AS count FROM source_availability").fetchone()["count"]
        items = conn.execute("SELECT COUNT(*) AS count FROM catalog_items WHERE media_type IN ('channel', 'movie', 'episode')").fetchone()["count"]
    return {"source_availability": sources, "average_sources_per_item": round(sources / items, 2) if items else 0}


def count_enabled_source_availability(catalog_internal_id: str) -> int:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM source_availability WHERE catalog_internal_id = ? AND enabled = 1",
            (catalog_internal_id,),
        ).fetchone()
    return int(row["count"] if row else 0)


def clear_test_data() -> CatalogSummary:
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM source_availability")
        conn.execute("DELETE FROM catalog_sources")
        conn.execute("DELETE FROM catalog_items")
        conn.execute("DELETE FROM catalog_imports")
        conn.commit()
    add_log("warning", "catalog", "Cleared catalog test data")
    return get_summary()


def _redact_url(url: str) -> str:
    return re.sub(r"(/(?:live|movie|series)/)[^/]+/[^/]+/", r"\1[redacted]/[redacted]/", url, flags=re.IGNORECASE)
