import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from app.core.config import get_settings
from app.services.catalog import ensure_schema
from app.services.source_entry_ledger import (
    ObservationSpool,
    SourceObservation,
    content_fingerprint,
    provider_entry_identity,
    sanitized_text,
    source_identity_from_feed_id,
)


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

    def test_feed_identity_requires_and_preserves_an_opaque_uuid(self):
        feed_id = str(uuid4())
        identity = source_identity_from_feed_id(feed_id)
        self.assertEqual(identity, source_identity_from_feed_id(feed_id))
        self.assertTrue(identity.startswith("feed_"))
        self.assertIsNone(source_identity_from_feed_id(None))
        self.assertIsNone(source_identity_from_feed_id("https://user:secret@example.test/list"))

    def test_provider_identifiers_are_typed_hashes(self):
        identity, kind = provider_entry_identity({
            "cuid": " Movie&#45;123 ", "tvg-id": "ignored",
        })
        self.assertEqual(kind, "cuid")
        self.assertRegex(identity, r"^cuid:[0-9a-f]{64}$")
        self.assertNotIn("Movie", identity)
        tvg_identity, tvg_kind = provider_entry_identity({"tvg-id": "ABC"})
        self.assertEqual(tvg_kind, "tvg_id")
        self.assertEqual(tvg_identity, provider_entry_identity({"tvg-id": "abc"})[0])

    def test_media_aware_fingerprints_reject_title_only_movies(self):
        self.assertIsNone(content_fingerprint(
            "movie", title="Same Title", attrs={},
        ))
        movie = content_fingerprint(
            "movie", title="Same Title", attrs={"year": "2024"},
        )
        self.assertRegex(movie, r"^[0-9a-f]{64}$")
        episode = content_fingerprint(
            "episode", title="Episode", attrs={}, series_name="The Show",
            season_number=1, episode_number=2, episode_title="Pilot",
        )
        self.assertRegex(episode, r"^[0-9a-f]{64}$")
        self.assertIsNone(content_fingerprint(
            "episode", title="Episode", attrs={}, series_name="The Show",
            season_number=None, episode_number=2,
        ))

    def test_spool_is_owner_only_sanitized_and_removable(self):
        spool = ObservationSpool(get_settings().data_dir)
        try:
            mode = spool.path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)
            title = sanitized_text(
                "Movie https://user:secret@example.test/a?token=bad /private/file"
            )
            spool.append(SourceObservation(
                placement_index=0, media_type="movie", observed_title=title,
                normalized_series_name=None, season_number=None, episode_number=None,
                provider_entry_id=None, provider_entry_id_kind=None,
                content_fingerprint=None, observed_catalog_item_id="movie_one",
                observed_parent_catalog_item_id=None,
                observed_source_availability_id=1, observed_at="2026-01-01T00:00:00",
            ))
            raw = spool.path.read_text()
            self.assertNotIn("example.test", raw)
            self.assertNotIn("/private/file", raw)
            self.assertNotIn("secret", raw)
            self.assertEqual(len(list(spool.observations())), 1)
        finally:
            path = spool.path
            spool.cleanup()
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
