import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.core.config import get_settings
from app.services.catalog import ensure_schema


class SourceEntryLedgerSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        os.environ.pop("MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED", None)
        get_settings.cache_clear()

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        os.environ.pop("MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED", None)
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(get_settings().data_dir / "media_router.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_feature_defaults_off_and_schema_creation_is_idempotent(self):
        self.assertFalse(get_settings().source_entry_shadow_ledger_enabled)
        ensure_schema()
        ensure_schema()
        with self.connect() as conn:
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({
                "source_import_runs", "source_entries", "source_entry_occurrences",
            }.issubset(tables))
            indexes = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            self.assertIn("idx_source_occurrences_provider_entry", indexes)
            self.assertFalse(any(name.startswith("sqlite_autoindex_source_entries_2")
                                 for name in indexes))

    def test_occurrence_identity_is_bounded_and_entry_is_nullable(self):
        ensure_schema()
        with self.connect() as conn:
            columns = {
                row["name"]: row for row in
                conn.execute("PRAGMA table_info(source_entry_occurrences)")
            }
            self.assertEqual(columns["source_entry_id"]["notnull"], 0)
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='source_entry_occurrences'"
            ).fetchone()["sql"]
            self.assertIn("'provider_id', 'fingerprint', 'identity_poor'", sql)
            self.assertIn(
                "UNIQUE(import_run_id, source_identity, placement_index)", sql,
            )


if __name__ == "__main__":
    unittest.main()
