from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.core.config import get_settings


CURRENT_SCHEMA_VERSION = 1
CURRENT_MIGRATION_NAME = "consolidated_additive_schema"


class DatabaseMigrationError(RuntimeError):
    """Raised when the persistent database cannot be upgraded safely."""


def database_path() -> Path:
    return get_settings().data_dir / "media_router.db"


def migrate_database(path: Path | None = None) -> int:
    """Bring a database to the current schema and record verified completion.

    The feature-owned initializers remain the source of the additive schema for
    this first consolidated migration. They are run on one connection in a
    deterministic dependency order. A version is recorded only after SQLite's
    integrity and foreign-key checks pass, so a partial attempt is visible as
    unversioned and can be retried safely.
    """
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                applied_at TEXT NOT NULL
            )"""
        )
        applied = {
            int(row["version"])
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        unknown = [version for version in applied if version > CURRENT_SCHEMA_VERSION]
        if unknown:
            raise DatabaseMigrationError(
                f"Database schema version {max(unknown)} is newer than supported version {CURRENT_SCHEMA_VERSION}."
            )

        if CURRENT_SCHEMA_VERSION not in applied:
            # Imports are local to avoid making the persistence boundary part of
            # each feature module's import graph.
            from app.services.catalog import ensure_schema
            from app.services.broker import ensure_broker_schema
            from app.services.outputs import ensure_outputs_schema
            from app.services.emby import ensure_emby_schema
            from app.services.source_entry_ledger import ensure_source_entry_schema

            ensure_schema(conn)
            # Catalog import deliberately treats this observation-only schema as
            # fail-open. The startup migration boundary must still verify that a
            # complete current schema can be installed before recording success.
            ensure_source_entry_schema(conn)
            ensure_broker_schema(conn)
            ensure_outputs_schema(conn)
            ensure_emby_schema(conn)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise DatabaseMigrationError(f"SQLite integrity check failed: {integrity}")
            foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise DatabaseMigrationError(
                    f"SQLite foreign-key check failed for {len(foreign_key_errors)} row(s)."
                )
            conn.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?,?,?)",
                (
                    CURRENT_SCHEMA_VERSION,
                    CURRENT_MIGRATION_NAME,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        return CURRENT_SCHEMA_VERSION
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
