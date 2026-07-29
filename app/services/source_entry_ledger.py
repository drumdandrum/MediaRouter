from __future__ import annotations

from contextlib import closing
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
from uuid import UUID, uuid4


RUN_STATUSES = ("started", "completed", "failed")
VOD_MEDIA_TYPES = ("movie", "episode")
IDENTITY_STATES = ("provider_id", "fingerprint", "identity_poor")
MAX_TEXT_INPUT = 4096
MAX_OBSERVED_TEXT = 256
_URI_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,31}://\S+)")
_OPAQUE_URI_RE = re.compile(
    r"(?i)\b(?:file|ftp|plugin|rtmp|rtsp|smb|udp):[^\s]+"
)
_USERINFO_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.+-])(?:[a-z][a-z0-9+.-]{1,31}:)?"
    r"[^\s:@/]+:[^\s@/]+@[^\s/]+(?:/\S*)?"
)
_UNIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s/]+/)*[^\s/]*")
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\)[^\s]+")
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|token|auth(?:orization)?|password|passwd|secret)"
    r"\s*[=:]\s*[^\s,;]+"
)


class SourceFeedScopeConflict(RuntimeError):
    pass


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
        try:
            os.chmod(raw_path, 0o600)
            self.path = Path(raw_path)
            self._handle = os.fdopen(descriptor, "w", encoding="utf-8")
        except Exception:
            os.close(descriptor)
            Path(raw_path).unlink(missing_ok=True)
            raise

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
        try:
            self.close()
        finally:
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
    text = _USERINFO_RE.sub("[redacted]", text)
    text = _URI_RE.sub("[redacted]", text)
    text = _OPAQUE_URI_RE.sub("[redacted]", text)
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


def _ledger_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def register_or_validate_source_feed(
    db_path: Path,
    *,
    source_identity: str,
    provider_id: str | None,
    account_id: str | None,
    media_scope: str,
    observed_at: str,
) -> str:
    """Register a feed once or require exact, NULL-sensitive scope equality."""
    if media_scope not in {"movie", "episode", "vod"}:
        raise ValueError("unsupported source feed media scope")
    expected = (provider_id, account_id, media_scope)
    with closing(_ledger_connect(db_path)) as conn:
        with conn:
            row = conn.execute(
                """SELECT provider_id,account_id,media_scope
                   FROM source_feeds WHERE source_feed_id=?""",
                (source_identity,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO source_feeds
                       (source_feed_id,provider_id,account_id,media_scope,active,
                        created_at,updated_at)
                       VALUES (?,?,?,?,1,?,?)""",
                    (
                        source_identity, provider_id, account_id, media_scope,
                        observed_at, observed_at,
                    ),
                )
                return "registered"
            actual = (row["provider_id"], row["account_id"], row["media_scope"])
            if actual != expected:
                raise SourceFeedScopeConflict("source feed scope conflict")
            conn.execute(
                "UPDATE source_feeds SET active=1,updated_at=? WHERE source_feed_id=?",
                (observed_at, source_identity),
            )
            return "existing"


def start_import_run(
    db_path: Path,
    *,
    source_identity: str,
    job_id: str | None,
    provider_id: str | None,
    account_id: str | None,
    media_scope: str,
    started_at: str,
) -> str:
    import_run_id = uuid4().hex
    with closing(_ledger_connect(db_path)) as conn:
        with conn:
            conn.execute(
                """INSERT INTO source_import_runs
                   (import_run_id,source_identity,job_id,provider_id,account_id,media_scope,
                    status,catalog_import_completed,entry_count,occurrence_count,started_at)
                   VALUES (?,?,?,?,?,?,'started',0,0,0,?)""",
                (
                    import_run_id, source_identity, job_id, provider_id, account_id,
                    media_scope, started_at,
                ),
            )
    return import_run_id


def fail_import_run(
    db_path: Path,
    import_run_id: str,
    *,
    error_category: str,
    catalog_import_completed: bool,
    finished_at: str,
) -> None:
    category = re.sub(r"[^a-z0-9_]+", "_", error_category.casefold())[:64] or "error"
    with closing(_ledger_connect(db_path)) as conn:
        with conn:
            conn.execute(
                """UPDATE source_import_runs
                   SET status='failed',catalog_import_completed=?,error_category=?,
                       finished_at=?
                   WHERE import_run_id=? AND status='started'""",
                (int(catalog_import_completed), category, finished_at, import_run_id),
            )


def _matching_source_entry(
    conn: sqlite3.Connection,
    *,
    source_identity: str,
    observation: SourceObservation,
) -> tuple[str | None, str, bool]:
    if observation.provider_entry_id:
        rows = conn.execute(
            """SELECT source_entry_id FROM source_entries
               WHERE source_identity=? AND media_type=? AND provider_entry_id=?""",
            (
                source_identity, observation.media_type,
                observation.provider_entry_id,
            ),
        ).fetchall()
        return (
            rows[0]["source_entry_id"] if len(rows) == 1 else None,
            "provider_id",
            len(rows) == 0,
        )
    if observation.content_fingerprint:
        rows = conn.execute(
            """SELECT source_entry_id FROM source_entries
               WHERE source_identity=? AND media_type=? AND content_fingerprint=?""",
            (
                source_identity, observation.media_type,
                observation.content_fingerprint,
            ),
        ).fetchall()
        return (
            rows[0]["source_entry_id"] if len(rows) == 1 else None,
            "fingerprint",
            len(rows) == 0,
        )
    return None, "identity_poor", False


def finalize_import_run(
    db_path: Path,
    *,
    import_run_id: str,
    source_identity: str,
    provider_id: str | None,
    account_id: str | None,
    observations: Iterator[SourceObservation],
    finished_at: str,
) -> int:
    occurrence_count = 0
    with closing(_ledger_connect(db_path)) as conn, conn:
        run = conn.execute(
            """SELECT rowid AS run_sequence,* FROM source_import_runs
               WHERE import_run_id=? AND status='started'""",
            (import_run_id,),
        ).fetchone()
        if run is None:
            raise RuntimeError("source import run is not active")
        newer_completed = conn.execute(
            """SELECT 1 FROM source_import_runs
               WHERE source_identity=? AND status='completed' AND rowid>?
               LIMIT 1""",
            (source_identity, run["run_sequence"]),
        ).fetchone()
        if newer_completed is not None:
            conn.execute(
                """UPDATE source_import_runs
                   SET status='failed',catalog_import_completed=1,
                       error_category='superseded_by_newer_run',
                       entry_count=0,occurrence_count=0,finished_at=?
                   WHERE import_run_id=?""",
                (finished_at, import_run_id),
            )
            return 0
        for observation in observations:
            source_entry_id, identity_state, create_entry = _matching_source_entry(
                conn, source_identity=source_identity, observation=observation,
            )
            if source_entry_id is None and create_entry:
                source_entry_id = uuid4().hex
                conn.execute(
                    """INSERT INTO source_entries
                       (source_entry_id,source_identity,provider_id,account_id,
                        provider_entry_id,provider_entry_id_kind,media_type,
                        observed_catalog_item_id,observed_source_availability_id,
                        content_fingerprint,normalized_title,first_seen_import_run_id,
                        last_seen_import_run_id,active,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (
                        source_entry_id, source_identity, provider_id, account_id,
                        observation.provider_entry_id,
                        observation.provider_entry_id_kind, observation.media_type,
                        observation.observed_catalog_item_id,
                        observation.observed_source_availability_id,
                        observation.content_fingerprint,
                        normalized_title(observation.observed_title), import_run_id,
                        import_run_id, observation.observed_at,
                        observation.observed_at,
                    ),
                )
            elif source_entry_id is not None:
                conn.execute(
                    """UPDATE source_entries
                       SET provider_id=?,account_id=?,provider_entry_id=?,
                           provider_entry_id_kind=?,observed_catalog_item_id=?,
                           observed_source_availability_id=?,content_fingerprint=?,
                           normalized_title=?,last_seen_import_run_id=?,active=1,
                           updated_at=?
                       WHERE source_entry_id=?""",
                    (
                        provider_id, account_id, observation.provider_entry_id,
                        observation.provider_entry_id_kind,
                        observation.observed_catalog_item_id,
                        observation.observed_source_availability_id,
                        observation.content_fingerprint,
                        normalized_title(observation.observed_title), import_run_id,
                        observation.observed_at, source_entry_id,
                    ),
                )
            conn.execute(
                """INSERT INTO source_entry_occurrences
                   (import_run_id,source_entry_id,source_identity,placement_index,
                    media_type,identity_state,observed_title,normalized_series_name,
                    season_number,episode_number,provider_entry_id,
                    provider_entry_id_kind,content_fingerprint,
                    observed_catalog_item_id,observed_parent_catalog_item_id,
                    observed_source_availability_id,observed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    import_run_id, source_entry_id, source_identity,
                    observation.placement_index, observation.media_type,
                    identity_state, observation.observed_title,
                    observation.normalized_series_name, observation.season_number,
                    observation.episode_number, observation.provider_entry_id,
                    observation.provider_entry_id_kind,
                    observation.content_fingerprint,
                    observation.observed_catalog_item_id,
                    observation.observed_parent_catalog_item_id,
                    observation.observed_source_availability_id,
                    observation.observed_at,
                ),
            )
            occurrence_count += 1

        overlap = conn.execute(
            """SELECT 1 FROM source_import_runs
               WHERE source_identity=? AND status='started' AND rowid>?
               LIMIT 1""",
            (source_identity, run["run_sequence"]),
        ).fetchone()
        if occurrence_count > 0 and overlap is None:
            conn.execute(
                """UPDATE source_entries SET active=0,updated_at=?
                   WHERE source_identity=? AND active=1
                     AND last_seen_import_run_id!=?""",
                (finished_at, source_identity, import_run_id),
            )
        conn.execute(
            """UPDATE source_import_runs
               SET status='completed',catalog_import_completed=1,
                   entry_count=?,occurrence_count=?,finished_at=?
               WHERE import_run_id=?""",
            (occurrence_count, occurrence_count, finished_at, import_run_id),
        )
    return occurrence_count


def source_entry_diagnostics(db_path: Path, *, limit: int = 100) -> dict[str, Any]:
    """Return bounded, aggregate-only diagnostics from completed observations."""
    bounded_limit = max(1, min(int(limit), 500))
    queries = {
        "moved_entries": """
            SELECT COUNT(*) FROM (
              SELECT source_entry_id FROM source_entry_occurrences
              WHERE source_entry_id IS NOT NULL
              GROUP BY source_entry_id HAVING COUNT(DISTINCT placement_index)>1
            )""",
        "reused_positions": """
            SELECT COUNT(*) FROM (
              SELECT source_identity,placement_index FROM source_entry_occurrences
              WHERE source_entry_id IS NOT NULL
              GROUP BY source_identity,placement_index
              HAVING COUNT(DISTINCT source_entry_id)>1
            )""",
        "duplicate_provider_identifiers": """
            SELECT COUNT(*) FROM (
              SELECT import_run_id,provider_entry_id FROM source_entry_occurrences
              WHERE provider_entry_id IS NOT NULL
              GROUP BY import_run_id,provider_entry_id HAVING COUNT(*)>1
            )""",
        "duplicate_fingerprints": """
            SELECT COUNT(*) FROM (
              SELECT import_run_id,content_fingerprint FROM source_entry_occurrences
              WHERE content_fingerprint IS NOT NULL
              GROUP BY import_run_id,content_fingerprint HAVING COUNT(*)>1
            )""",
        "source_entry_catalog_drift": """
            SELECT COUNT(*) FROM (
              SELECT source_entry_id FROM source_entry_occurrences
              WHERE source_entry_id IS NOT NULL AND observed_catalog_item_id IS NOT NULL
              GROUP BY source_entry_id
              HAVING COUNT(DISTINCT observed_catalog_item_id)>1
            )""",
        "catalog_multi_source_entries": """
            SELECT COUNT(*) FROM (
              SELECT observed_catalog_item_id FROM source_entry_occurrences
              WHERE source_entry_id IS NOT NULL AND observed_catalog_item_id IS NOT NULL
              GROUP BY observed_catalog_item_id
              HAVING COUNT(DISTINCT source_entry_id)>1
            )""",
        "inactive_entries": "SELECT COUNT(*) FROM source_entries WHERE active=0",
        "fingerprint_changes_under_provider_id": """
            SELECT COUNT(*) FROM (
              SELECT source_entry_id FROM source_entry_occurrences
              WHERE source_entry_id IS NOT NULL AND provider_entry_id IS NOT NULL
                AND content_fingerprint IS NOT NULL
              GROUP BY source_entry_id
              HAVING COUNT(DISTINCT content_fingerprint)>1
            )""",
        "provider_id_changes_under_fingerprint": """
            SELECT COUNT(*) FROM (
              SELECT source_identity,content_fingerprint FROM source_entry_occurrences
              WHERE content_fingerprint IS NOT NULL AND provider_entry_id IS NOT NULL
              GROUP BY source_identity,content_fingerprint
              HAVING COUNT(DISTINCT provider_entry_id)>1
            )""",
        "availability_drift": """
            SELECT COUNT(*) FROM (
              SELECT source_entry_id FROM source_entry_occurrences
              WHERE source_entry_id IS NOT NULL
                AND observed_source_availability_id IS NOT NULL
              GROUP BY source_entry_id
              HAVING COUNT(DISTINCT observed_source_availability_id)>1
            )""",
        "structural_episode_conflicts": """
            SELECT COUNT(*) FROM (
              SELECT source_identity,normalized_series_name,season_number,episode_number
              FROM source_entry_occurrences
              WHERE media_type='episode' AND normalized_series_name IS NOT NULL
                AND season_number IS NOT NULL AND episode_number IS NOT NULL
                AND observed_catalog_item_id IS NOT NULL
              GROUP BY source_identity,normalized_series_name,season_number,episode_number
              HAVING COUNT(DISTINCT observed_catalog_item_id)>1
            )""",
        "identity_poor_occurrences": """
            SELECT COUNT(*) FROM source_entry_occurrences
            WHERE identity_state='identity_poor'""",
    }
    with closing(_ledger_connect(db_path)) as conn:
        counts = {
            name: int(conn.execute(query).fetchone()[0])
            for name, query in queries.items()
        }
        reappearances = conn.execute(
            """WITH completed AS (
                 SELECT import_run_id,source_identity,
                   ROW_NUMBER() OVER (
                     PARTITION BY source_identity ORDER BY started_at,import_run_id
                   ) AS run_number
                 FROM source_import_runs WHERE status='completed'
               ), seen AS (
                 SELECT o.source_entry_id,c.source_identity,c.run_number,
                   LAG(c.run_number) OVER (
                     PARTITION BY o.source_entry_id ORDER BY c.run_number
                   ) AS prior_run
                 FROM source_entry_occurrences o
                 JOIN completed c ON c.import_run_id=o.import_run_id
                 WHERE o.source_entry_id IS NOT NULL
               )
               SELECT COUNT(DISTINCT source_entry_id) FROM seen
               WHERE prior_run IS NOT NULL AND run_number-prior_run>1"""
        ).fetchone()[0]
        counts["reappearances"] = int(reappearances)
        recent_failed = [
            {
                "import_run_id": row["import_run_id"],
                "source_identity": row["source_identity"],
                "error_category": row["error_category"],
            }
            for row in conn.execute(
                """SELECT import_run_id,source_identity,error_category
                   FROM source_import_runs WHERE status='failed'
                   ORDER BY started_at DESC,import_run_id DESC LIMIT ?""",
                (bounded_limit,),
            ).fetchall()
        ]
    return {
        "counts": counts,
        "recent_failed_runs": recent_failed,
        "limit": bounded_limit,
    }


def ensure_source_entry_schema(conn: sqlite3.Connection) -> None:
    """Create the additive, observation-only source-entry ledger schema."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_feeds (
            source_feed_id TEXT PRIMARY KEY,
            provider_id TEXT,
            account_id TEXT,
            media_scope TEXT NOT NULL
                CHECK(media_scope IN ('movie', 'episode', 'vod')),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE SET NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_source_feeds_account_scope
            ON source_feeds(account_id, media_scope, active);
        CREATE INDEX IF NOT EXISTS idx_source_feeds_provider
            ON source_feeds(provider_id, active);

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
            FOREIGN KEY(source_identity) REFERENCES source_feeds(source_feed_id),
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
