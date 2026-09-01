import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db.migrations import CURRENT_SCHEMA_VERSION, DatabaseMigrationError, migrate_database
from app.db import migrations
from app.core.config import get_settings
from app.schemas.providers import ProviderCreate


V03_CATALOG_SCHEMA = """
CREATE TABLE catalog_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, internal_id TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL, title TEXT NOT NULL, normalized_title TEXT NOT NULL,
    group_title TEXT, tvg_id TEXT, tvg_name TEXT, tvg_logo TEXT, cuid TEXT,
    show_name TEXT, season_number INTEGER, episode_number INTEGER,
    episode_title TEXT, parent_internal_id TEXT, confidence TEXT NOT NULL DEFAULT 'high',
    raw_title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE catalog_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_internal_id TEXT NOT NULL,
    media_type TEXT NOT NULL, source_name TEXT NOT NULL, source_url TEXT NOT NULL,
    cuid TEXT, tvg_id TEXT, raw_extinf TEXT NOT NULL, first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL, UNIQUE(catalog_internal_id, source_url),
    FOREIGN KEY(catalog_internal_id) REFERENCES catalog_items(internal_id) ON DELETE CASCADE
);
CREATE TABLE catalog_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, source_name TEXT NOT NULL,
    file_path TEXT NOT NULL, status TEXT NOT NULL, summary_json TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
"""


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "media_router.db"

    def tearDown(self):
        self.temp.cleanup()

    def _tables(self):
        with sqlite3.connect(self.db) as conn:
            return {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}

    def test_fresh_initialization_is_complete_and_idempotent(self):
        self.assertEqual(CURRENT_SCHEMA_VERSION, migrate_database(self.db))
        expected = {
            "schema_migrations", "providers", "accounts", "catalog_items",
            "catalog_sources", "source_availability", "channel_placements",
            "broker_reservations", "broker_reservation_identity_aliases",
            "output_generated_files", "output_run_history",
            "emby_playback_bindings", "emby_channel_mappings", "emby_vod_item_mappings",
            "source_feeds", "source_import_runs", "source_entries", "source_entry_occurrences",
        }
        self.assertTrue(expected.issubset(self._tables()))
        with sqlite3.connect(self.db) as conn:
            before = conn.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
        self.assertEqual(CURRENT_SCHEMA_VERSION, migrate_database(self.db))
        with sqlite3.connect(self.db) as conn:
            after = conn.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
        self.assertEqual(before, after)

    def test_upgrade_from_real_v03_catalog_schema_preserves_identity_and_source(self):
        with sqlite3.connect(self.db) as conn:
            conn.executescript(V03_CATALOG_SCHEMA)
            conn.execute(
                """INSERT INTO catalog_items
                (internal_id,media_type,title,normalized_title,cuid,confidence,created_at,updated_at)
                VALUES ('movie_stable','movie','Stable Movie','stable movie','legacy-cuid','high','2026-01-01','2026-01-01')"""
            )
            conn.execute(
                """INSERT INTO catalog_sources
                (catalog_internal_id,media_type,source_name,source_url,cuid,raw_extinf,first_seen_at,last_seen_at)
                VALUES ('movie_stable','movie','Legacy','https://provider.invalid/movie/1','legacy-cuid','#EXTINF','2026-01-01','2026-01-01')"""
            )
        migrate_database(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            item = conn.execute("SELECT * FROM catalog_items WHERE internal_id='movie_stable'").fetchone()
            source = conn.execute(
                "SELECT * FROM source_availability WHERE catalog_internal_id='movie_stable'"
            ).fetchone()
            self.assertEqual("legacy-cuid", item["cuid"])
            self.assertEqual("https://provider.invalid/movie/1", source["location_ref"])
            self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])

    def test_upgrade_preserves_current_authoritative_and_historical_rows(self):
        migrate_database(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM schema_migrations")
            conn.execute(
                "INSERT INTO providers VALUES ('provider-1','Provider','IPTV','notes',1,'Healthy','2026-01-01','2026-01-01')"
            )
            conn.execute(
                """INSERT INTO accounts
                (id,provider_id,friendly_name,username,password_secret,base_url,playlist_url,
                 max_simultaneous_streams,priority_group,weight,enabled,health_status,notes,created_at,updated_at)
                VALUES ('account-1','provider-1','Account','user','secret','https://provider.invalid','',2,
                        'Preferred',90,1,'Healthy','notes','2026-01-01','2026-01-01')"""
            )
            conn.execute(
                """INSERT INTO catalog_items
                (internal_id,media_type,title,normalized_title,confidence,created_at,updated_at)
                VALUES ('movie-1','movie','Movie','movie','high','2026-01-01','2026-01-01')"""
            )
            conn.execute(
                """INSERT INTO source_availability
                (id,catalog_internal_id,provider_id,account_id,location_ref,media_type,enabled,last_seen_at,
                 metadata_confidence,notes,raw_extinf,created_at,updated_at)
                VALUES (41,'movie-1','provider-1','account-1','https://provider.invalid/movie/1','movie',1,
                        '2026-01-01','high','','','2026-01-01','2026-01-01')"""
            )
            conn.execute(
                """INSERT INTO broker_reservations
                (reservation_id,catalog_item_id,source_availability_id,provider_id,account_id,media_type,
                 location_ref,status,created_at,expires_at,lifecycle_state,request_count,distinct_activity_count)
                VALUES ('reservation-1','movie-1',41,'provider-1','account-1','movie','https://provider.invalid/movie/1',
                        'released','2026-01-01','2026-01-02','released',1,0)"""
            )
            conn.execute(
                """INSERT INTO emby_vod_item_mappings
                VALUES ('emby-test','item-1','source-1','movie-1','movie','exact_runtime_path','2026-01-01','2026-01-01')"""
            )
            conn.execute(
                """INSERT INTO output_run_history
                (output_id,output_type,mode,status,summary_json,started_at,finished_at)
                VALUES ('strm','strm','generate','complete','{}','2026-01-01','2026-01-01')"""
            )
        migrate_database(self.db)
        migrate_database(self.db)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(('user', 'secret', 2, 90), conn.execute(
                "SELECT username,password_secret,max_simultaneous_streams,weight FROM accounts WHERE id='account-1'"
            ).fetchone())
            self.assertEqual((41, 'movie-1'), conn.execute(
                "SELECT id,catalog_internal_id FROM source_availability WHERE id=41"
            ).fetchone())
            self.assertEqual(1, conn.execute(
                "SELECT COUNT(*) FROM broker_reservations WHERE reservation_id='reservation-1'"
            ).fetchone()[0])
            self.assertEqual(1, conn.execute(
                "SELECT COUNT(*) FROM emby_vod_item_mappings WHERE emby_item_id='item-1'"
            ).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM output_run_history").fetchone()[0])

    def test_failed_upgrade_is_not_marked_applied_and_can_retry(self):
        with patch("app.services.catalog.ensure_schema", side_effect=sqlite3.OperationalError("synthetic migration failure")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic migration failure"):
                migrate_database(self.db)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
        self.assertEqual(CURRENT_SCHEMA_VERSION, migrate_database(self.db))

    def test_failure_after_partial_schema_rolls_back_entire_version(self):
        with patch(
            "app.services.outputs.ensure_outputs_schema",
            side_effect=sqlite3.OperationalError("synthetic late migration failure"),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic late migration failure"):
                migrate_database(self.db)
        with sqlite3.connect(self.db) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            self.assertEqual({"schema_migrations"}, tables)
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
        self.assertEqual(CURRENT_SCHEMA_VERSION, migrate_database(self.db))

    def test_integrity_failure_rolls_back_and_version_is_recorded_last(self):
        original_verify = migrations._verify_database
        observed_versions = []

        def fail_verification(connection):
            observed_versions.append(
                connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            )
            raise DatabaseMigrationError("synthetic integrity failure")

        with patch.object(migrations, "_verify_database", side_effect=fail_verification):
            with self.assertRaisesRegex(DatabaseMigrationError, "synthetic integrity failure"):
                migrate_database(self.db)
        self.assertEqual([0], observed_versions)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
            self.assertEqual(0, conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='catalog_items'"
            ).fetchone()[0])

        with patch.object(migrations, "_verify_database", side_effect=original_verify):
            migrate_database(self.db)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])

    def test_newer_schema_is_refused(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)"
            )
            conn.execute("INSERT INTO schema_migrations VALUES (999,'future','2026-01-01')")
        with self.assertRaisesRegex(DatabaseMigrationError, "newer than supported"):
            migrate_database(self.db)

    def test_services_can_read_and_write_after_migrated_database_reopens(self):
        data_dir = Path(self.temp.name) / "service-data"
        with patch.dict(os.environ, {"MEDIA_ROUTER_DATA_DIR": str(data_dir)}):
            get_settings.cache_clear()
            try:
                migrate_database()
                # migrate_database has closed its connection; these calls open
                # fresh service connections just as a restarted app would.
                from app.services.catalog import get_summary
                from app.services.providers import create_provider, list_providers

                self.assertEqual(0, get_summary().total_items)
                created = create_provider(ProviderCreate(friendly_name="Restart Provider", provider_type="IPTV"))
                self.assertEqual(created.id, list_providers()[0].id)
            finally:
                get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
