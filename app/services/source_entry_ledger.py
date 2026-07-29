from __future__ import annotations

import sqlite3


RUN_STATUSES = ("started", "completed", "failed")
VOD_MEDIA_TYPES = ("movie", "episode")
IDENTITY_STATES = ("provider_id", "fingerprint", "identity_poor")


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
