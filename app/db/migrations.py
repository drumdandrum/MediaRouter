from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.core.config import get_settings


CURRENT_SCHEMA_VERSION = 1
CURRENT_MIGRATION_NAME = "consolidated_additive_schema"


class DatabaseMigrationError(RuntimeError):
    """Raised when the persistent database cannot be upgraded safely."""


class _MigrationConnection:
    """Defer helper commits and avoid executescript's implicit COMMIT."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def commit(self) -> None:
        # The version runner owns the transaction boundary.
        return None

    def executescript(self, script: str) -> None:
        statement = ""
        for character in script:
            statement += character
            if character == ";" and sqlite3.complete_statement(statement):
                self._connection.execute(statement)
                statement = ""
        if statement.strip():
            raise DatabaseMigrationError("Migration schema script ended with an incomplete SQL statement.")


def database_path() -> Path:
    return get_settings().data_dir / "media_router.db"


def _apply_version_1(conn: _MigrationConnection) -> None:
    from app.services.catalog import ensure_schema
    from app.services.broker import ensure_broker_schema
    from app.services.outputs import ensure_outputs_schema
    from app.services.emby import ensure_emby_schema
    from app.services.source_entry_ledger import ensure_source_entry_schema

    ensure_schema(conn)
    # Catalog import deliberately treats this observation-only schema as
    # fail-open. The startup migration boundary must still require it.
    ensure_source_entry_schema(conn)
    ensure_broker_schema(conn)
    ensure_outputs_schema(conn)
    ensure_emby_schema(conn)


MIGRATIONS = {1: (CURRENT_MIGRATION_NAME, _apply_version_1)}


def _verify_database(conn: sqlite3.Connection) -> None:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise DatabaseMigrationError(f"SQLite integrity check failed: {integrity}")
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise DatabaseMigrationError(
            f"SQLite foreign-key check failed for {len(foreign_key_errors)} row(s)."
        )


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

        if applied and applied != set(range(1, max(applied) + 1)):
            raise DatabaseMigrationError("Database migration history is incomplete or non-contiguous.")

        current = max(applied, default=0)
        conn.commit()
        for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
            name, apply_migration = MIGRATIONS[version]
            try:
                conn.execute("BEGIN IMMEDIATE")
                apply_migration(_MigrationConnection(conn))
                _verify_database(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?,?,?)",
                    (version, name, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return CURRENT_SCHEMA_VERSION
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
