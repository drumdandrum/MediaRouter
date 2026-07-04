from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from app.core.config import get_settings
from app.schemas.catalog import CatalogItem, CatalogSource, CatalogSummary
from app.services.jobs import update_job
from app.services.logs import add_log


EXTINF_RE = re.compile(r'^#EXTINF:[^,]*?(?P<attrs>(?:\s+[A-Za-z0-9_-]+="[^"]*")*)\s*,(?P<title>.*)$')
ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
EPISODE_RE = re.compile(r"(?P<show>.+?)[\s._-]+S(?P<season>\d{1,2})E(?P<episode>\d{1,3})(?:[\s._-]+(?P<title>.+))?$", re.IGNORECASE)
PREFIX_RE = re.compile(r"^[A-Z]{2,4}\s*-\s*")


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

        CREATE INDEX IF NOT EXISTS idx_catalog_items_type ON catalog_items(media_type);
        CREATE INDEX IF NOT EXISTS idx_catalog_items_parent ON catalog_items(parent_internal_id);
        CREATE INDEX IF NOT EXISTS idx_catalog_sources_item ON catalog_sources(catalog_internal_id);
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


def _media_type_from_context(path: Path, url: str, attrs: dict[str, str], title: str) -> str:
    probe = f"{path.name} {url} {attrs.get('group-title', '')} {title}".lower()
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


def parse_m3u(path: Path, source_name: str) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []
    raw_lines = path.read_text(errors="ignore").splitlines()
    current: tuple[dict[str, str], str, str] | None = None
    for line in raw_lines:
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
            media_type = _media_type_from_context(path, stripped, attrs, title)
            show, season, episode, episode_title, confidence = (None, None, None, None, "high")
            if media_type == "episode":
                show, season, episode, episode_title, confidence = _parse_series(attrs.get("tvg-name") or title)
            entries.append(
                ParsedEntry(
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
            )
            current = None
    return entries


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


def _upsert_item(conn: sqlite3.Connection, *, internal_id: str, media_type: str, title: str, entry: ParsedEntry, parent_internal_id: str | None = None) -> None:
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
                tvg_id=:tvg_id, tvg_name=:tvg_name, tvg_logo=:tvg_logo, cuid=:cuid,
                show_name=:show_name, season_number=:season_number, episode_number=:episode_number,
                episode_title=:episode_title, parent_internal_id=:parent_internal_id,
                confidence=:confidence, raw_title=:raw_title, updated_at=:updated_at
            WHERE internal_id=:internal_id
            """,
            values,
        )
    else:
        values["created_at"] = now
        conn.execute(
            """
            INSERT INTO catalog_items (
                internal_id, media_type, title, normalized_title, group_title, tvg_id, tvg_name,
                tvg_logo, cuid, show_name, season_number, episode_number, episode_title,
                parent_internal_id, confidence, raw_title, created_at, updated_at
            ) VALUES (
                :internal_id, :media_type, :title, :normalized_title, :group_title, :tvg_id, :tvg_name,
                :tvg_logo, :cuid, :show_name, :season_number, :episode_number, :episode_title,
                :parent_internal_id, :confidence, :raw_title, :created_at, :updated_at
            )
            """,
            values,
        )


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


def import_paths(paths: list[str], source_name: str, job_id: str | None = None) -> dict[str, Any]:
    ensure_schema()
    summary = {"files": 0, "entries": 0, "channels": 0, "movies": 0, "series": 0, "episodes": 0, "sources": 0}
    with _connect() as conn:
        for index, raw_path in enumerate(paths):
            path = Path(raw_path)
            if not path.exists():
                raise FileNotFoundError(f"Playlist not found: {raw_path}")
            entries = parse_m3u(path, source_name)
            summary["files"] += 1
            summary["entries"] += len(entries)
            if job_id:
                update_job(job_id, status="running", progress=min(90, 10 + (index * 80 // max(len(paths), 1))), message=f"Importing {path.name}")
            for entry in entries:
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
                    _upsert_item(conn, internal_id=parent_id, media_type="series", title=series_title, entry=series_entry)
                _upsert_item(conn, internal_id=internal_id, media_type=item_type, title=title, entry=entry, parent_internal_id=parent_id)
                _upsert_source(conn, internal_id=internal_id, media_type=item_type, entry=entry, source_name=source_name)
            imported_at = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO catalog_imports (job_id, source_name, file_path, status, summary_json, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, source_name, str(path), "completed", json.dumps(summary), imported_at),
            )
        conn.commit()
    add_log("info", "catalog", f"Imported {summary['entries']} catalog entries from {summary['files']} playlist file(s)")
    return get_summary().model_dump()


def run_catalog_import_job(job_id: str, paths: list[str], source_name: str) -> None:
    try:
        update_job(job_id, status="running", progress=5, message="Starting catalog import")
        summary = import_paths(paths, source_name, job_id)
        update_job(job_id, status="complete", progress=100, message=f"Catalog import complete: {summary['sources']} source mappings")
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


def get_summary() -> CatalogSummary:
    ensure_schema()
    with _connect() as conn:
        counts = {
            row["media_type"]: row["count"]
            for row in conn.execute("SELECT media_type, COUNT(*) AS count FROM catalog_items GROUP BY media_type").fetchall()
        }
        sources = conn.execute("SELECT COUNT(*) AS count FROM catalog_sources").fetchone()["count"]
        last = conn.execute("SELECT MAX(imported_at) AS last_import_time FROM catalog_imports").fetchone()["last_import_time"]
    return CatalogSummary(
        channels=counts.get("channel", 0),
        movies=counts.get("movie", 0),
        series=counts.get("series", 0),
        episodes=counts.get("episode", 0),
        sources=sources,
        last_import_time=datetime.fromisoformat(last) if last else None,
    )


def list_items(media_type: str) -> list[CatalogItem]:
    ensure_schema()
    rows = _rows("SELECT * FROM catalog_items WHERE media_type = ? ORDER BY title", (media_type,))
    return [_item_from_row(row) for row in rows]


def list_sources() -> list[CatalogSource]:
    ensure_schema()
    rows = _rows("SELECT * FROM catalog_sources ORDER BY last_seen_at DESC, source_name, id")
    return [_source_from_row(row) for row in rows]


def clear_test_data() -> CatalogSummary:
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM catalog_sources")
        conn.execute("DELETE FROM catalog_items")
        conn.execute("DELETE FROM catalog_imports")
        conn.commit()
    add_log("warning", "catalog", "Cleared catalog test data")
    return get_summary()


def _redact_url(url: str) -> str:
    return re.sub(r"(/(?:live|movie|series)/)[^/]+/[^/]+/", r"\1[redacted]/[redacted]/", url, flags=re.IGNORECASE)
