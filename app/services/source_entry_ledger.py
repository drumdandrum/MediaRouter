from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
from typing import Any, Iterator
from uuid import UUID


RUN_STATUSES = ("started", "completed", "failed")
VOD_MEDIA_TYPES = ("movie", "episode")
IDENTITY_STATES = ("provider_id", "fingerprint", "identity_poor")
MAX_TEXT_INPUT = 4096
MAX_OBSERVED_TEXT = 256
_URI_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,31}://\S+)")
_UNIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s/]+/)*[^\s/]*")
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\)[^\s]+")
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|password|passwd|secret)"
    r"\s*[=:]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class SourceObservation:
    placement_index: int
    media_type: str
    observed_title: str
    normalized_series_name: str | None
    season_number: int | None
    episode_number: int | None
    provider_entry_id: str | None
    provider_entry_id_kind: str | None
    content_fingerprint: str | None
    observed_catalog_item_id: str
    observed_parent_catalog_item_id: str | None
    observed_source_availability_id: int
    observed_at: str


class ObservationSpool:
    """Owner-only JSON-lines spool containing only bounded, sanitized facts."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".source-entry-observations-", suffix=".jsonl", dir=directory,
        )
        os.chmod(raw_path, 0o600)
        self.path = Path(raw_path)
        self._handle = os.fdopen(descriptor, "w", encoding="utf-8")

    def append(self, observation: SourceObservation) -> None:
        self._handle.write(json.dumps(
            asdict(observation), ensure_ascii=True, separators=(",", ":"),
        ) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def observations(self) -> Iterator[SourceObservation]:
        self.close()
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)
                yield SourceObservation(**payload)

    def cleanup(self) -> None:
        self.close()
        self.path.unlink(missing_ok=True)


def source_identity_from_feed_id(feed_id: str | None) -> str | None:
    """Return a safe feed identity only for an explicit opaque UUID."""
    if not feed_id:
        return None
    try:
        parsed = UUID(str(feed_id).strip())
    except (ValueError, TypeError, AttributeError):
        return None
    return f"feed_{parsed.hex}"


def _normalized_evidence(value: Any, *, casefold: bool = True) -> str:
    text = str(value or "")[:MAX_TEXT_INPUT]
    previous = None
    for _ in range(4):
        if text == previous:
            break
        previous, text = text, html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold() if casefold else text


def sanitized_text(value: Any) -> str:
    text = _normalized_evidence(value, casefold=False)
    text = _SECRET_RE.sub("[redacted]", text)
    text = _URI_RE.sub("[redacted]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted]", text)
    text = _UNIX_PATH_RE.sub("[redacted]", text)
    return text[:MAX_OBSERVED_TEXT] or "Untitled"


def provider_entry_identity(attrs: dict[str, str]) -> tuple[str | None, str | None]:
    for raw_kind, kind in (("cuid", "cuid"), ("tvg-id", "tvg_id")):
        normalized = _normalized_evidence(attrs.get(raw_kind))
        if normalized:
            digest = hashlib.sha256(
                f"source-provider-id-v1\0{kind}\0{normalized}".encode("utf-8")
            ).hexdigest()
            return f"{kind}:{digest}", kind
    return None, None


def normalized_title(value: Any) -> str:
    return _normalized_evidence(sanitized_text(value))


def content_fingerprint(
    media_type: str,
    *,
    title: Any,
    attrs: dict[str, str],
    series_name: Any = None,
    season_number: int | None = None,
    episode_number: int | None = None,
    episode_title: Any = None,
) -> str | None:
    if media_type == "movie":
        normalized_movie_title = normalized_title(title)
        year = ""
        for key in ("year", "release-year", "release_year"):
            candidate = _normalized_evidence(attrs.get(key))
            if re.fullmatch(r"\d{4}", candidate):
                year = candidate
                break
        if not normalized_movie_title or not year:
            return None
        canonical = ("movie-v1", normalized_movie_title, year)
    elif media_type == "episode":
        normalized_series = normalized_title(series_name)
        if (
            not normalized_series
            or season_number is None
            or episode_number is None
            or season_number < 0
            or episode_number < 0
        ):
            return None
        canonical = (
            "episode-v1", normalized_series, str(season_number),
            str(episode_number), normalized_title(episode_title) if episode_title else "",
        )
    else:
        return None
    encoded = "\0".join(canonical).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensure_source_entry_schema(conn: sqlite3.Connection) -> None:
    """Create the additive, observation-only source-entry ledger schema."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_import_runs (
            import_run_id TEXT PRIMARY KEY,
            source_identity TEXT NOT NULL,
            job_id TEXT,
            catalog_import_id INTEGER,
            provider_id TEXT,
            account_id TEXT,
            media_scope TEXT NOT NULL
                CHECK(media_scope IN ('movie', 'episode', 'vod')),
            status TEXT NOT NULL
                CHECK(status IN ('started', 'completed', 'failed')),
            catalog_import_completed INTEGER NOT NULL DEFAULT 0
                CHECK(catalog_import_completed IN (0, 1)),
            entry_count INTEGER NOT NULL DEFAULT 0 CHECK(entry_count >= 0),
            occurrence_count INTEGER NOT NULL DEFAULT 0 CHECK(occurrence_count >= 0),
            error_category TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY(catalog_import_id) REFERENCES catalog_imports(id) ON DELETE SET NULL,
            FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE SET NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_source_import_runs_source
            ON source_import_runs(source_identity, started_at);
        CREATE INDEX IF NOT EXISTS idx_source_import_runs_status
            ON source_import_runs(status, started_at);
        CREATE INDEX IF NOT EXISTS idx_source_import_runs_account
            ON source_import_runs(account_id, media_scope, started_at);

        CREATE TABLE IF NOT EXISTS source_entries (
            source_entry_id TEXT PRIMARY KEY,
            source_identity TEXT NOT NULL,
            provider_id TEXT,
            account_id TEXT,
            provider_entry_id TEXT,
            provider_entry_id_kind TEXT
                CHECK(provider_entry_id_kind IS NULL OR provider_entry_id_kind IN ('cuid', 'tvg_id')),
            media_type TEXT NOT NULL CHECK(media_type IN ('movie', 'episode')),
            observed_catalog_item_id TEXT,
            observed_source_availability_id INTEGER,
            content_fingerprint TEXT,
            normalized_title TEXT NOT NULL,
            first_seen_import_run_id TEXT NOT NULL,
            last_seen_import_run_id TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE SET NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE SET NULL,
            FOREIGN KEY(observed_catalog_item_id) REFERENCES catalog_items(internal_id) ON DELETE SET NULL,
            FOREIGN KEY(observed_source_availability_id) REFERENCES source_availability(id) ON DELETE SET NULL,
            FOREIGN KEY(first_seen_import_run_id) REFERENCES source_import_runs(import_run_id),
            FOREIGN KEY(last_seen_import_run_id) REFERENCES source_import_runs(import_run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_entries_source
            ON source_entries(source_identity, active);
        CREATE INDEX IF NOT EXISTS idx_source_entries_provider_entry
            ON source_entries(source_identity, media_type, provider_entry_id);
        CREATE INDEX IF NOT EXISTS idx_source_entries_fingerprint
            ON source_entries(source_identity, media_type, content_fingerprint);
        CREATE INDEX IF NOT EXISTS idx_source_entries_catalog
            ON source_entries(observed_catalog_item_id, active);
        CREATE INDEX IF NOT EXISTS idx_source_entries_availability
            ON source_entries(observed_source_availability_id);

        CREATE TABLE IF NOT EXISTS source_entry_occurrences (
            occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_run_id TEXT NOT NULL,
            source_entry_id TEXT,
            source_identity TEXT NOT NULL,
            placement_index INTEGER NOT NULL CHECK(placement_index >= 0),
            media_type TEXT NOT NULL CHECK(media_type IN ('movie', 'episode')),
            identity_state TEXT NOT NULL
                CHECK(identity_state IN ('provider_id', 'fingerprint', 'identity_poor')),
            observed_title TEXT NOT NULL,
            normalized_series_name TEXT,
            season_number INTEGER,
            episode_number INTEGER,
            provider_entry_id TEXT,
            provider_entry_id_kind TEXT
                CHECK(provider_entry_id_kind IS NULL OR provider_entry_id_kind IN ('cuid', 'tvg_id')),
            content_fingerprint TEXT,
            observed_catalog_item_id TEXT,
            observed_parent_catalog_item_id TEXT,
            observed_source_availability_id INTEGER,
            observed_at TEXT NOT NULL,
            UNIQUE(import_run_id, source_identity, placement_index),
            FOREIGN KEY(import_run_id) REFERENCES source_import_runs(import_run_id) ON DELETE CASCADE,
            FOREIGN KEY(source_entry_id) REFERENCES source_entries(source_entry_id) ON DELETE CASCADE,
            FOREIGN KEY(observed_catalog_item_id) REFERENCES catalog_items(internal_id) ON DELETE SET NULL,
            FOREIGN KEY(observed_parent_catalog_item_id) REFERENCES catalog_items(internal_id) ON DELETE SET NULL,
            FOREIGN KEY(observed_source_availability_id) REFERENCES source_availability(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_source_occurrences_entry
            ON source_entry_occurrences(source_entry_id, import_run_id);
        CREATE INDEX IF NOT EXISTS idx_source_occurrences_position
            ON source_entry_occurrences(source_identity, placement_index, import_run_id);
        CREATE INDEX IF NOT EXISTS idx_source_occurrences_provider_entry
            ON source_entry_occurrences(source_identity, provider_entry_id);
        CREATE INDEX IF NOT EXISTS idx_source_occurrences_fingerprint
            ON source_entry_occurrences(source_identity, content_fingerprint);
        CREATE INDEX IF NOT EXISTS idx_source_occurrences_catalog
            ON source_entry_occurrences(observed_catalog_item_id);
        """
    )
