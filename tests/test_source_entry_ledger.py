import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.core.config import get_settings
from app.services.catalog import ensure_schema, import_paths
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

    def enable_ledger(self):
        os.environ["MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED"] = "true"
        get_settings.cache_clear()

    def playlist(self, name: str, entries: list[tuple[str, str, str]]) -> Path:
        path = Path(self.temp.name) / name
        lines = ["#EXTM3U"]
        for attrs, title, url in entries:
            lines.extend([f"#EXTINF:-1 {attrs},{title}", url])
        path.write_text("\n".join(lines) + "\n")
        return path

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

    def test_disabled_import_produces_no_ledger_rows(self):
        playlist = self.playlist("disabled.m3u", [
            ('cuid="movie-1" year="2024"', "Movie One", "https://one.invalid/a"),
        ])
        import_paths([str(playlist)], "Test", media_type_hint="movie",
                     source_feed_id=str(uuid4()))
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_import_runs"
            ).fetchone()[0], 0)

    def test_enabled_import_records_hashed_observations_and_cleans_spool(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        secret_url = "https://user:password@example.invalid/movie?token=secret"
        playlist = self.playlist("enabled.m3u", [
            ('cuid="movie-1" year="2024"', "Movie One", secret_url),
            ('year="2023"', "Title Only With Year", "https://example.invalid/two"),
            ("", "Identity Poor", "https://example.invalid/three"),
        ])
        import_paths([str(playlist)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        with self.connect() as conn:
            run = conn.execute("SELECT * FROM source_import_runs").fetchone()
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["occurrence_count"], 3)
            occurrences = conn.execute(
                "SELECT * FROM source_entry_occurrences ORDER BY placement_index"
            ).fetchall()
            self.assertEqual(occurrences[0]["identity_state"], "provider_id")
            self.assertRegex(occurrences[0]["provider_entry_id"], r"^cuid:[0-9a-f]{64}$")
            self.assertEqual(occurrences[1]["identity_state"], "fingerprint")
            self.assertEqual(occurrences[2]["identity_state"], "identity_poor")
            self.assertIsNone(occurrences[2]["source_entry_id"])
            ledger_text = "\n".join(
                str(tuple(row)) for table in (
                    "source_import_runs", "source_entries", "source_entry_occurrences"
                ) for row in conn.execute(f"SELECT * FROM {table}")
            )
            for forbidden in (
                "example.invalid", "password", "token=secret",
                str(playlist), "#EXTINF",
            ):
                self.assertNotIn(forbidden, ledger_text)
        self.assertFalse(list(get_settings().data_dir.glob(
            ".source-entry-observations-*.jsonl"
        )))

    def test_reorder_removal_and_reappearance_are_observational(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        path = self.playlist("changes.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        path = self.playlist("changes.m3u", [
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        with self.connect() as conn:
            entries = conn.execute(
                "SELECT provider_entry_id,active FROM source_entries"
            ).fetchall()
            self.assertEqual(sorted(row["active"] for row in entries), [0, 1])
        path = self.playlist("changes.m3u", [
            ('cuid="one"', "One Renamed", "https://example.invalid/one"),
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entries WHERE active=1"
            ).fetchone()[0], 2)
            positions = conn.execute(
                """SELECT provider_entry_id,placement_index
                   FROM source_entry_occurrences
                   WHERE import_run_id=(SELECT import_run_id FROM source_import_runs
                     WHERE status='completed' ORDER BY started_at DESC LIMIT 1)
                   ORDER BY placement_index"""
            ).fetchall()
            self.assertEqual([row["placement_index"] for row in positions], [0, 1])

    def test_duplicate_provider_id_is_retained_as_two_occurrences(self):
        self.enable_ledger()
        path = self.playlist("duplicates.m3u", [
            ('cuid="duplicate"', "First", "https://example.invalid/one"),
            ('cuid="duplicate"', "Second", "https://example.invalid/two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=str(uuid4()))
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entry_occurrences"
            ).fetchone()[0], 2)
            self.assertEqual(conn.execute(
                "SELECT COUNT(DISTINCT source_entry_id) FROM source_entry_occurrences"
            ).fetchone()[0], 1)

    def test_episode_structure_and_incomplete_episode_are_observed_safely(self):
        self.enable_ledger()
        path = self.playlist("episodes.m3u", [
            ("", "Show Name S01E02 Pilot", "https://example.invalid/episode"),
            ("", "Unstructured Episode", "https://example.invalid/unknown"),
        ])
        import_paths([str(path)], "Test", media_type_hint="episode",
                     source_feed_id=str(uuid4()))
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM source_entry_occurrences
                   ORDER BY placement_index"""
            ).fetchall()
            self.assertEqual(rows[0]["identity_state"], "fingerprint")
            self.assertEqual((rows[0]["season_number"], rows[0]["episode_number"]), (1, 2))
            self.assertIsNotNone(rows[0]["observed_parent_catalog_item_id"])
            self.assertEqual(rows[1]["identity_state"], "identity_poor")
            self.assertIsNone(rows[1]["content_fingerprint"])

    def test_ledger_failure_does_not_rollback_successful_catalog_import(self):
        self.enable_ledger()
        path = self.playlist("failure.m3u", [
            ('cuid="survives"', "Survives", "https://example.invalid/survives"),
        ])
        with patch(
            "app.services.catalog.finalize_import_run",
            side_effect=sqlite3.OperationalError("forced ledger failure"),
        ):
            summary = import_paths(
                [str(path)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE media_type='movie'"
            ).fetchone()[0], 1)
            run = conn.execute("SELECT * FROM source_import_runs").fetchone()
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["catalog_import_completed"], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entry_occurrences"
            ).fetchone()[0], 0)

    def test_missing_or_multiple_feed_identity_skips_observation(self):
        self.enable_ledger()
        first = self.playlist("first.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
        ])
        second = self.playlist("second.m3u", [
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        import_paths([str(first)], "Test", media_type_hint="movie")
        import_paths(
            [str(first), str(second)], "Test", media_type_hint="movie",
            source_feed_id=str(uuid4()),
        )
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_import_runs"
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
