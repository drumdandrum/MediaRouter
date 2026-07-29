import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

from app.core.config import get_settings
from app.services.catalog import ensure_schema, import_paths
from app.schemas.outputs import LiveM3uSettingsUpdate, StrmSettingsUpdate
from app.services.outputs import (
    generate_live_m3u_output,
    generate_strm_outputs,
    update_live_m3u_settings,
    update_strm_settings,
)
from app.services.source_entry_ledger import (
    ObservationSpool,
    SourceObservation,
    SourceFeedScopeConflict,
    MAX_FINALIZE_OBSERVATIONS,
    SHADOW_BUSY_TIMEOUT_MS,
    content_fingerprint,
    finalize_import_run,
    provider_entry_identity,
    register_or_validate_source_feed,
    sanitized_text,
    source_entry_diagnostics,
    source_identity_from_feed_id,
    start_import_run,
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

    def observed(self, title: str) -> SourceObservation:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM source_entry_occurrences
                   WHERE observed_title=? ORDER BY occurrence_id DESC LIMIT 1""",
                (title,),
            ).fetchone()
        return SourceObservation(
            placement_index=row["placement_index"],
            media_type=row["media_type"],
            observed_title=row["observed_title"],
            normalized_series_name=row["normalized_series_name"],
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            provider_entry_id=row["provider_entry_id"],
            provider_entry_id_kind=row["provider_entry_id_kind"],
            content_fingerprint=row["content_fingerprint"],
            observed_catalog_item_id=row["observed_catalog_item_id"],
            observed_parent_catalog_item_id=row["observed_parent_catalog_item_id"],
            observed_source_availability_id=row["observed_source_availability_id"],
            observed_at=row["observed_at"],
        )

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
                "source_feeds", "source_import_runs", "source_entries",
                "source_entry_occurrences",
            }.issubset(tables))
            indexes = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            self.assertIn("idx_source_occurrences_provider_entry", indexes)
            self.assertFalse(any(name.startswith("sqlite_autoindex_source_entries_2")
                                 for name in indexes))

    def add_provider_account(self, provider_id: str, account_id: str) -> None:
        ensure_schema()
        now = "2026-01-01T00:00:00"
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO providers
                   (id,friendly_name,provider_type,notes,enabled,health_status,
                    created_at,updated_at)
                   VALUES (?,?,'IPTV','',1,'Unknown',?,?)""",
                (provider_id, provider_id, now, now),
            )
            conn.execute(
                """INSERT INTO accounts
                   (id,provider_id,friendly_name,created_at,updated_at)
                   VALUES (?,?,?,?,?)""",
                (account_id, provider_id, account_id, now, now),
            )

    def test_source_feed_schema_and_registration_are_idempotent(self):
        ensure_schema()
        ensure_schema()
        feed = source_identity_from_feed_id(str(uuid4()))
        db_path = get_settings().data_dir / "media_router.db"
        first = register_or_validate_source_feed(
            db_path, source_identity=feed, provider_id=None, account_id=None,
            media_scope="movie", observed_at="2026-01-01T00:00:00",
        )
        second = register_or_validate_source_feed(
            db_path, source_identity=feed, provider_id=None, account_id=None,
            media_scope="movie", observed_at="2026-01-02T00:00:00",
        )
        self.assertEqual((first, second), ("registered", "existing"))
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM source_feeds").fetchone()
            self.assertEqual(row["source_feed_id"], feed)
            self.assertEqual(row["created_at"], "2026-01-01T00:00:00")
            self.assertEqual(row["updated_at"], "2026-01-02T00:00:00")
            columns = {
                item["name"] for item in conn.execute("PRAGMA table_info(source_feeds)")
            }
            self.assertEqual(columns, {
                "source_feed_id", "provider_id", "account_id", "media_scope",
                "active", "created_at", "updated_at",
            })

    def test_feed_scope_conflicts_never_rebind_and_null_is_not_a_wildcard(self):
        self.add_provider_account("provider-one", "account-one")
        self.add_provider_account("provider-two", "account-two")
        db_path = get_settings().data_dir / "media_router.db"

        def assert_conflict(original, conflicting):
            feed = source_identity_from_feed_id(str(uuid4()))
            register_or_validate_source_feed(
                db_path, source_identity=feed, observed_at="2026-01-01T00:00:00",
                **original,
            )
            with self.assertRaises(SourceFeedScopeConflict):
                register_or_validate_source_feed(
                    db_path, source_identity=feed,
                    observed_at="2026-01-02T00:00:00", **conflicting,
                )
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM source_feeds WHERE source_feed_id=?", (feed,)
                ).fetchone()
                self.assertEqual(
                    (row["provider_id"], row["account_id"], row["media_scope"]),
                    (
                        original["provider_id"], original["account_id"],
                        original["media_scope"],
                    ),
                )

        assert_conflict(
            dict(provider_id="provider-one", account_id="account-one",
                 media_scope="movie"),
            dict(provider_id="provider-two", account_id="account-one",
                 media_scope="movie"),
        )
        assert_conflict(
            dict(provider_id="provider-one", account_id="account-one",
                 media_scope="movie"),
            dict(provider_id="provider-one", account_id="account-two",
                 media_scope="movie"),
        )
        assert_conflict(
            dict(provider_id="provider-one", account_id="account-one",
                 media_scope="movie"),
            dict(provider_id="provider-one", account_id="account-one",
                 media_scope="episode"),
        )
        assert_conflict(
            dict(provider_id=None, account_id=None, media_scope="movie"),
            dict(provider_id="provider-one", account_id=None, media_scope="movie"),
        )
        assert_conflict(
            dict(provider_id="provider-one", account_id=None, media_scope="movie"),
            dict(provider_id=None, account_id=None, media_scope="movie"),
        )

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
                "Movie https://user:secret@example.test/a?token=bad /private/file "
                "plugin:value token=standalone"
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
            self.assertNotIn("plugin:value", raw)
            self.assertNotIn("standalone", raw)
            self.assertEqual(len(list(spool.observations())), 1)
        finally:
            path = spool.path
            spool.cleanup()
        self.assertFalse(path.exists())

    def test_sanitizer_redacts_userinfo_without_destroying_human_titles(self):
        unsafe = (
            "user:password@example.test",
            "custom:user:password@example.test/path?token=bad",
            "CuStOm://user:password@example.test/path?token=bad",
        )
        for value in unsafe:
            sanitized = sanitized_text(value)
            self.assertNotIn("password", sanitized)
            self.assertNotIn("example.test", sanitized)
        for safe in (
            "person@example.test", "Face/Off", "go:home", "John 3:16",
            "S01:E02", "Title: Subtitle",
        ):
            self.assertEqual(sanitized_text(safe), safe)

    def test_disabled_import_produces_no_ledger_rows(self):
        playlist = self.playlist("disabled.m3u", [
            ('cuid="movie-1" year="2024"', "Movie One", "https://one.invalid/a"),
        ])
        with patch(
            "app.services.catalog.register_or_validate_source_feed",
            side_effect=AssertionError("disabled imports must not access feed registry"),
        ):
            import_paths([str(playlist)], "Test", media_type_hint="movie",
                         source_feed_id=str(uuid4()))
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_import_runs"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_feeds"
            ).fetchone()[0], 0)

    def test_shadow_schema_failure_cannot_fail_enabled_or_disabled_imports(self):
        playlist = self.playlist("schema-failure.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
        ])
        with patch(
            "app.services.catalog.ensure_source_entry_schema",
            side_effect=sqlite3.OperationalError("forced shadow schema failure"),
        ):
            disabled = import_paths(
                [str(playlist)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(disabled["entries"], 1)

        self.enable_ledger()
        second = self.playlist("schema-failure-enabled.m3u", [
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        with patch(
            "app.services.catalog.ensure_source_entry_schema",
            side_effect=sqlite3.OperationalError("forced shadow schema failure"),
        ):
            enabled = import_paths(
                [str(second)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(enabled["entries"], 1)

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
                    "source_feeds", "source_import_runs", "source_entries",
                    "source_entry_occurrences",
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

    def test_registry_failure_isolated_before_run_or_spool_creation(self):
        self.enable_ledger()
        playlist = self.playlist("registry-failure.m3u", [
            ('cuid="survives"', "Survives", "https://example.invalid/survives"),
        ])
        with patch(
            "app.services.catalog.register_or_validate_source_feed",
            side_effect=sqlite3.OperationalError("forced registry failure"),
        ):
            summary = import_paths(
                [str(playlist)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE media_type='movie'"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_feeds"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_import_runs"
            ).fetchone()[0], 0)
        self.assertFalse(list(get_settings().data_dir.glob(
            ".source-entry-observations-*.jsonl"
        )))

    def test_run_start_failure_does_not_affect_authoritative_import(self):
        self.enable_ledger()
        playlist = self.playlist("run-start-failure.m3u", [
            ('cuid="survives"', "Survives", "https://example.invalid/survives"),
        ])
        with patch(
            "app.services.catalog.start_import_run",
            side_effect=sqlite3.OperationalError("forced run-start failure"),
        ):
            summary = import_paths(
                [str(playlist)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE media_type='movie'"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_import_runs"
            ).fetchone()[0], 0)
        self.assertFalse(list(get_settings().data_dir.glob(
            ".source-entry-observations-*.jsonl"
        )))

    def test_failed_status_write_cannot_mask_successful_catalog_import(self):
        self.enable_ledger()
        playlist = self.playlist("status-failure.m3u", [
            ('cuid="survives"', "Survives", "https://example.invalid/survives"),
        ])
        with (
            patch(
                "app.services.catalog.finalize_import_run",
                side_effect=sqlite3.OperationalError("forced finalization failure"),
            ),
            patch(
                "app.services.catalog.fail_import_run",
                side_effect=sqlite3.OperationalError("forced status failure"),
            ),
        ):
            summary = import_paths(
                [str(playlist)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE media_type='movie'"
            ).fetchone()[0], 1)
        self.assertFalse(list(get_settings().data_dir.glob(
            ".source-entry-observations-*.jsonl"
        )))

    def test_observation_construction_failure_cannot_fail_catalog_import(self):
        self.enable_ledger()
        playlist = self.playlist("observation-failure.m3u", [
            ('cuid="survives"', "Survives", "https://example.invalid/survives"),
        ])
        with patch(
            "app.services.catalog.content_fingerprint",
            side_effect=ValueError("malformed observation"),
        ):
            summary = import_paths(
                [str(playlist)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE media_type='movie'"
            ).fetchone()[0], 1)
            run = conn.execute("SELECT * FROM source_import_runs").fetchone()
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_category"], "spool_write_failed")

    def test_authoritative_failure_still_removes_spool(self):
        self.enable_ledger()
        playlist = self.playlist("authoritative-failure.m3u", [
            ('cuid="fails"', "Fails", "https://example.invalid/fails"),
        ])
        with patch(
            "app.services.catalog._import_paths_authoritative",
            side_effect=ValueError("forced authoritative failure"),
        ):
            with self.assertRaises(ValueError):
                import_paths(
                    [str(playlist)], "Test", media_type_hint="movie",
                    source_feed_id=str(uuid4()),
                )
        self.assertFalse(list(get_settings().data_dir.glob(
            ".source-entry-observations-*.jsonl"
        )))

    def test_spool_constructor_and_close_failures_attempt_cleanup(self):
        self.enable_ledger()
        playlist = self.playlist("spool-constructor-failure.m3u", [
            ('cuid="survives"', "Survives", "https://example.invalid/survives"),
        ])
        with patch(
            "app.services.source_entry_ledger.os.chmod",
            side_effect=OSError("forced chmod failure"),
        ):
            summary = import_paths(
                [str(playlist)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        self.assertFalse(list(get_settings().data_dir.glob(
            ".source-entry-observations-*.jsonl"
        )))

        spool = ObservationSpool(get_settings().data_dir)
        spool_path = spool.path
        spool._handle.close()
        with patch.object(spool, "close", side_effect=OSError("forced close failure")):
            with self.assertRaises(OSError):
                spool.cleanup()
        self.assertFalse(spool_path.exists())

    def test_malformed_spool_and_cleanup_failures_do_not_change_catalog_result(self):
        self.enable_ledger()
        playlist = self.playlist("spool-read-failure.m3u", [
            ('cuid="survives"', "Survives", "https://example.invalid/survives"),
        ])
        with patch(
            "app.services.source_entry_ledger.ObservationSpool.observations",
            side_effect=ValueError("malformed spool line"),
        ):
            summary = import_paths(
                [str(playlist)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        with self.connect() as conn:
            run = conn.execute("SELECT * FROM source_import_runs").fetchone()
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["catalog_import_completed"], 1)

        second = self.playlist("cleanup-failure.m3u", [
            ('cuid="also-survives"', "Also Survives",
             "https://example.invalid/also-survives"),
        ])
        with patch(
            "app.services.source_entry_ledger.ObservationSpool.cleanup",
            side_effect=OSError("forced cleanup failure"),
        ):
            summary = import_paths(
                [str(second)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        for spool_path in get_settings().data_dir.glob(
            ".source-entry-observations-*.jsonl"
        ):
            spool_path.unlink()

    def test_registered_feed_scope_conflict_skips_only_shadow_observation(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        first = self.playlist("feed-movie.m3u", [
            ('cuid="movie"', "Movie", "https://example.invalid/movie"),
        ])
        second = self.playlist("feed-episode.m3u", [
            ("", "Show S01E02 Pilot", "https://example.invalid/episode"),
        ])
        import_paths([str(first)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        summary = import_paths([str(second)], "Test", media_type_hint="episode",
                               source_feed_id=feed_id)
        self.assertEqual(summary["entries"], 1)
        with self.connect() as conn:
            feed = conn.execute("SELECT * FROM source_feeds").fetchone()
            self.assertEqual(feed["media_scope"], "movie")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_import_runs"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE media_type='episode'"
            ).fetchone()[0], 1)
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

    def test_ambiguous_existing_provider_entries_do_not_create_another_entry(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        path = self.playlist("ambiguous-provider.m3u", [
            ('cuid="duplicate"', "First", "https://example.invalid/one"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        with self.connect() as conn:
            original = conn.execute("SELECT * FROM source_entries").fetchone()
            columns = [row["name"] for row in conn.execute(
                "PRAGMA table_info(source_entries)"
            )]
            values = [original[column] for column in columns]
            values[columns.index("source_entry_id")] = uuid4().hex
            conn.execute(
                f"INSERT INTO source_entries ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                values,
            )
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entries"
            ).fetchone()[0], 2)
            latest = conn.execute(
                """SELECT o.* FROM source_entry_occurrences o
                   JOIN source_import_runs r ON r.import_run_id=o.import_run_id
                   ORDER BY r.started_at DESC LIMIT 1"""
            ).fetchone()
            self.assertEqual(latest["identity_state"], "provider_id")
            self.assertIsNone(latest["source_entry_id"])

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
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_feeds"
            ).fetchone()[0], 0)

    def test_token_rotation_keeps_entry_identity_and_exposes_availability_drift(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        path = self.playlist("tokens.m3u", [
            ('cuid="stable" year="2024"', "Stable",
             "https://example.invalid/movie?token=one"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        path = self.playlist("tokens.m3u", [
            ('cuid="stable" year="2024"', "Stable",
             "https://example.invalid/movie?token=two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_feeds"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entries"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_availability"
            ).fetchone()[0], 2)
        diagnostics = source_entry_diagnostics(
            get_settings().data_dir / "media_router.db"
        )
        self.assertEqual(diagnostics["counts"]["availability_drift"], 1)

    def test_multiple_feeds_keep_distinct_logical_entries(self):
        self.enable_ledger()
        path = self.playlist("multi-feed.m3u", [
            ('cuid="same"', "Same", "https://example.invalid/same"),
        ])
        for feed_id in (str(uuid4()), str(uuid4())):
            import_paths([str(path)], "Test", media_type_hint="movie",
                         source_feed_id=feed_id)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_feeds"
            ).fetchone()[0], 2)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entries"
            ).fetchone()[0], 2)
        diagnostics = source_entry_diagnostics(
            get_settings().data_dir / "media_router.db"
        )
        self.assertEqual(
            diagnostics["counts"]["catalog_multi_source_entries"], 1,
        )

    def test_failed_and_overlapping_runs_do_not_deactivate_entries(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("overlap.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        with patch(
            "app.services.catalog._import_paths_authoritative",
            side_effect=ValueError("forced parser failure"),
        ):
            with self.assertRaises(ValueError):
                import_paths([str(path)], "Test", media_type_hint="movie",
                             source_feed_id=feed_id)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entries WHERE active=1"
            ).fetchone()[0], 2)
            failed = conn.execute(
                "SELECT * FROM source_import_runs WHERE status='failed'"
            ).fetchone()
            self.assertEqual(failed["catalog_import_completed"], 0)
        overlapping_run = start_import_run(
            get_settings().data_dir / "media_router.db",
            source_identity=source_identity, job_id=None, provider_id=None,
            account_id=None, media_scope="movie",
            started_at="9999-01-01T00:00:00",
        )
        path = self.playlist("overlap.m3u", [
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entries WHERE active=1"
            ).fetchone()[0], 1)
            conn.execute(
                """UPDATE source_import_runs SET status='failed',finished_at=started_at,
                   error_category='test_cleanup' WHERE import_run_id=?""",
                (overlapping_run,),
            )

    def test_newer_run_supersedes_older_late_completion_and_stale_started_run(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("out-of-order.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        one = self.observed("One")
        two = self.observed("Two")
        db_path = get_settings().data_dir / "media_router.db"
        older = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:01",
        )
        newer = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:02",
        )
        finalize_import_run(
            db_path, import_run_id=newer, source_identity=source_identity,
            provider_id=None, account_id=None,
            observations=iter([replace(two, placement_index=0)]),
            finished_at="2026-01-01T00:00:03",
        )
        result = finalize_import_run(
            db_path, import_run_id=older, source_identity=source_identity,
            provider_id=None, account_id=None,
            observations=iter([one, two]),
            finished_at="2026-01-01T00:00:04",
        )
        self.assertEqual(result, 0)
        with self.connect() as conn:
            rows = {
                row["normalized_title"]: row
                for row in conn.execute("SELECT * FROM source_entries")
            }
            self.assertEqual(rows["one"]["active"], 0)
            self.assertEqual(rows["two"]["active"], 1)
            old_run = conn.execute(
                "SELECT * FROM source_import_runs WHERE import_run_id=?", (older,)
            ).fetchone()
            self.assertEqual(old_run["status"], "failed")
            self.assertEqual(
                old_run["error_category"], "superseded_by_newer_run",
            )
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entry_occurrences WHERE import_run_id=?",
                (older,),
            ).fetchone()[0], 0)

    def test_newer_started_run_defers_older_deactivation_until_newer_finishes(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("ordered-overlap.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
            ('cuid="two"', "Two", "https://example.invalid/two"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        one = self.observed("One")
        two = self.observed("Two")
        db_path = get_settings().data_dir / "media_router.db"
        older = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:01",
        )
        newer = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:02",
        )
        finalize_import_run(
            db_path, import_run_id=older, source_identity=source_identity,
            provider_id=None, account_id=None, observations=iter([one]),
            finished_at="2026-01-01T00:00:03",
        )
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entries WHERE active=1"
            ).fetchone()[0], 2)
        finalize_import_run(
            db_path, import_run_id=newer, source_identity=source_identity,
            provider_id=None, account_id=None,
            observations=iter([replace(two, placement_index=0)]),
            finished_at="2026-01-01T00:00:04",
        )
        with self.connect() as conn:
            rows = {
                row["normalized_title"]: row["active"]
                for row in conn.execute("SELECT normalized_title,active FROM source_entries")
            }
            self.assertEqual(rows, {"one": 0, "two": 1})

    def test_zero_observation_run_does_not_deactivate_existing_entries(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("zero-observation.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        db_path = get_settings().data_dir / "media_router.db"
        run_id = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:01",
        )
        self.assertEqual(finalize_import_run(
            db_path, import_run_id=run_id, source_identity=source_identity,
            provider_id=None, account_id=None, observations=iter(()),
            finished_at="2026-01-01T00:00:02",
        ), 0)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entries WHERE active=1"
            ).fetchone()[0], 1)

    def test_finalization_prepares_observations_before_opening_write_connection(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("prepare-before-write.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        observation = self.observed("One")
        db_path = get_settings().data_dir / "media_router.db"
        run_id = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:01",
        )
        consumed = []

        def observations():
            consumed.append(True)
            yield observation

        from app.services import source_entry_ledger as ledger_module
        original_connect = ledger_module._ledger_connect

        def checked_connect(path):
            self.assertEqual(consumed, [True])
            return original_connect(path)

        with patch.object(ledger_module, "_ledger_connect", side_effect=checked_connect):
            finalize_import_run(
                db_path, import_run_id=run_id, source_identity=source_identity,
                provider_id=None, account_id=None, observations=observations(),
                finished_at="2026-01-01T00:00:02",
            )

    def test_finalization_limit_fails_before_opening_database(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("observation-limit.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        observation = self.observed("One")
        db_path = get_settings().data_dir / "media_router.db"
        run_id = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:01",
        )
        with (
            patch(
                "app.services.source_entry_ledger._ledger_connect",
                side_effect=AssertionError("database opened before limit validation"),
            ),
            self.assertRaisesRegex(ValueError, "observation limit"),
        ):
            finalize_import_run(
                db_path, import_run_id=run_id, source_identity=source_identity,
                provider_id=None, account_id=None,
                observations=(
                    observation for _ in range(MAX_FINALIZE_OBSERVATIONS + 1)
                ),
                finished_at="2026-01-01T00:00:02",
            )

    def test_shadow_writer_lock_timeout_is_short_and_rolls_back_cleanly(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("writer-timeout.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        observation = self.observed("One")
        db_path = get_settings().data_dir / "media_router.db"
        run_id = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:01",
        )
        writer = sqlite3.connect(db_path)
        writer.execute(
            "UPDATE source_feeds SET updated_at=updated_at WHERE source_feed_id=?",
            (source_identity,),
        )
        started = time.monotonic()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                finalize_import_run(
                    db_path, import_run_id=run_id,
                    source_identity=source_identity, provider_id=None,
                    account_id=None, observations=iter([observation]),
                    finished_at="2026-01-01T00:00:02",
                )
        finally:
            writer.rollback()
            writer.close()
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, SHADOW_BUSY_TIMEOUT_MS / 1000 * 0.75)
        self.assertLess(elapsed, 0.5)
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_entry_occurrences WHERE import_run_id=?",
                (run_id,),
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT status FROM source_import_runs WHERE import_run_id=?",
                (run_id,),
            ).fetchone()[0], "started")

    def test_failure_at_each_finalization_stage_rolls_back_entire_snapshot(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("stage-failures.m3u", [
            ('cuid="one"', "One", "https://example.invalid/one"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        observation = self.observed("One")
        db_path = get_settings().data_dir / "media_router.db"
        stages = (
            "SELECT source_entry_id FROM source_entries",
            "INSERT INTO source_entry_occurrences",
            "UPDATE source_entries SET active=0",
            "UPDATE source_import_runs SET status='completed'",
            "COMMIT",
        )
        from app.services import source_entry_ledger as ledger_module

        for sequence, stage in enumerate(stages, start=1):
            with self.subTest(stage=stage):
                run_id = start_import_run(
                    db_path, source_identity=source_identity, job_id=None,
                    provider_id=None, account_id=None, media_scope="movie",
                    started_at=f"2026-01-01T00:00:{sequence + 1:02d}",
                )

                class FailingConnection:
                    def __init__(self):
                        self.connection = sqlite3.connect(db_path)
                        self.connection.row_factory = sqlite3.Row
                        self.connection.execute("PRAGMA foreign_keys=ON")
                        self.connection.execute(
                            f"PRAGMA busy_timeout={SHADOW_BUSY_TIMEOUT_MS}"
                        )

                    def execute(self, sql, parameters=()):
                        normalized = " ".join(sql.split())
                        if stage != "COMMIT" and stage in normalized:
                            raise sqlite3.OperationalError(
                                f"forced {stage} failure"
                            )
                        return self.connection.execute(sql, parameters)

                    def __enter__(self):
                        return self

                    def __exit__(self, exc_type, exc, traceback):
                        if exc_type is not None:
                            self.connection.rollback()
                            return False
                        if stage == "COMMIT":
                            self.connection.rollback()
                            raise sqlite3.OperationalError(
                                "forced commit failure"
                            )
                        self.connection.commit()
                        return False

                    def close(self):
                        self.connection.close()

                with (
                    patch.object(
                        ledger_module, "_ledger_connect",
                        side_effect=lambda path: FailingConnection(),
                    ),
                    self.assertRaises(sqlite3.OperationalError),
                ):
                    finalize_import_run(
                        db_path, import_run_id=run_id,
                        source_identity=source_identity, provider_id=None,
                        account_id=None, observations=iter([observation]),
                        finished_at="2026-01-01T00:01:00",
                    )
                with self.connect() as conn:
                    self.assertEqual(conn.execute(
                        """SELECT COUNT(*) FROM source_entry_occurrences
                           WHERE import_run_id=?""",
                        (run_id,),
                    ).fetchone()[0], 0)
                    self.assertEqual(conn.execute(
                        "SELECT status FROM source_import_runs WHERE import_run_id=?",
                        (run_id,),
                    ).fetchone()[0], "started")
                    conn.execute(
                        """UPDATE source_import_runs
                           SET status='failed',error_category='test_cleanup',
                               finished_at=started_at
                           WHERE import_run_id=?""",
                        (run_id,),
                    )

    def test_concurrent_same_feed_finalizers_preserve_newest_snapshot(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        source_identity = source_identity_from_feed_id(feed_id)
        path = self.playlist("concurrent-finalizers.m3u", [
            ('cuid="baseline"', "Baseline", "https://example.invalid/baseline"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        baseline = self.observed("Baseline")
        db_path = get_settings().data_dir / "media_router.db"
        older = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:01",
        )
        newer = start_import_run(
            db_path, source_identity=source_identity, job_id=None,
            provider_id=None, account_id=None, media_scope="movie",
            started_at="2026-01-01T00:00:02",
        )
        older_observation = replace(
            baseline, observed_title="Older",
            provider_entry_id=f"cuid:{'1' * 64}", placement_index=0,
        )
        newer_observation = replace(
            baseline, observed_title="Newer",
            provider_entry_id=f"cuid:{'2' * 64}", placement_index=0,
        )
        barrier = threading.Barrier(2)
        errors = []

        def finish(run_id, observation, finished_at):
            try:
                barrier.wait()
                finalize_import_run(
                    db_path, import_run_id=run_id,
                    source_identity=source_identity, provider_id=None,
                    account_id=None, observations=iter([observation]),
                    finished_at=finished_at,
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(
                target=finish,
                args=(older, older_observation, "2026-01-01T00:00:03"),
            ),
            threading.Thread(
                target=finish,
                args=(newer, newer_observation, "2026-01-01T00:00:04"),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        with self.connect() as conn:
            active = conn.execute(
                "SELECT normalized_title FROM source_entries WHERE active=1"
            ).fetchall()
            self.assertEqual([row["normalized_title"] for row in active], ["newer"])

    def test_diagnostics_report_position_duplicates_changes_and_reappearance(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        path = self.playlist("diagnostics.m3u", [
            ('cuid="one" year="2024"', "Original", "https://example.invalid/one"),
            ('cuid="duplicate" year="2024"', "Duplicate", "https://example.invalid/d1"),
            ('cuid="duplicate" year="2024"', "Duplicate", "https://example.invalid/d2"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        path = self.playlist("diagnostics.m3u", [
            ('cuid="replacement" year="2024"', "Replacement", "https://example.invalid/r"),
            ('cuid="one" year="2025"', "Changed", "https://example.invalid/one"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        path = self.playlist("diagnostics.m3u", [
            ('cuid="one" year="2025"', "Changed", "https://example.invalid/one"),
            ('cuid="duplicate" year="2024"', "Duplicate", "https://example.invalid/d1"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        diagnostics = source_entry_diagnostics(
            get_settings().data_dir / "media_router.db", limit=10,
        )
        counts = diagnostics["counts"]
        self.assertGreaterEqual(counts["moved_entries"], 1)
        self.assertGreaterEqual(counts["reused_positions"], 1)
        self.assertGreaterEqual(counts["duplicate_provider_identifiers"], 1)
        self.assertGreaterEqual(counts["fingerprint_changes_under_provider_id"], 1)
        self.assertGreaterEqual(counts["reappearances"], 1)
        self.assertLessEqual(diagnostics["limit"], 500)

    def test_provider_id_change_under_stable_fingerprint_and_duplicate_fingerprint(self):
        self.enable_ledger()
        feed_id = str(uuid4())
        path = self.playlist("fingerprints.m3u", [
            ('cuid="old" year="2024"', "Same", "https://example.invalid/old"),
            ('year="2024"', "Duplicate Fingerprint", "https://example.invalid/a"),
            ('year="2024"', "Duplicate Fingerprint", "https://example.invalid/b"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        path = self.playlist("fingerprints.m3u", [
            ('cuid="new" year="2024"', "Same", "https://example.invalid/new"),
        ])
        import_paths([str(path)], "Test", media_type_hint="movie",
                     source_feed_id=feed_id)
        counts = source_entry_diagnostics(
            get_settings().data_dir / "media_router.db"
        )["counts"]
        self.assertGreaterEqual(counts["duplicate_fingerprints"], 1)
        self.assertGreaterEqual(counts["provider_id_changes_under_fingerprint"], 1)

    def test_spool_write_failure_is_isolated_and_cleaned(self):
        self.enable_ledger()
        path = self.playlist("spool-failure.m3u", [
            ('cuid="safe"', "Safe", "https://example.invalid/safe"),
        ])
        with patch(
            "app.services.source_entry_ledger.ObservationSpool.append",
            side_effect=OSError("forced spool failure"),
        ):
            summary = import_paths(
                [str(path)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
        self.assertEqual(summary["entries"], 1)
        with self.connect() as conn:
            run = conn.execute("SELECT * FROM source_import_runs").fetchone()
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_category"], "spool_write_failed")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE media_type='movie'"
            ).fetchone()[0], 1)
        self.assertFalse(list(get_settings().data_dir.glob(
            ".source-entry-observations-*.jsonl"
        )))

    def test_catalog_import_and_output_behavior_are_unchanged(self):
        path = self.playlist("parity.m3u", [
            ('cuid="parity" year="2024"', "Parity Movie",
             "https://example.invalid/parity"),
        ])
        disabled_summary = import_paths(
            [str(path)], "Test", media_type_hint="movie",
            source_feed_id=str(uuid4()),
        )
        output = Path(self.temp.name) / "outputs"
        movies = output / "movies"
        series = output / "series"
        movies.mkdir(parents=True)
        series.mkdir()
        update_strm_settings(StrmSettingsUpdate(
            movies_output_directory=str(movies),
            series_output_directory=str(series),
            generation_mode="Custom", maximum_movies=10, maximum_episodes=10,
        ))
        generate_strm_outputs("http://localhost:8088")
        before = {
            file.relative_to(output): file.read_bytes()
            for file in output.rglob("*.strm")
        }
        self.enable_ledger()
        enabled_summary = import_paths(
            [str(path)], "Test", media_type_hint="movie",
            source_feed_id=str(uuid4()),
        )
        generate_strm_outputs("http://localhost:8088")
        after = {
            file.relative_to(output): file.read_bytes()
            for file in output.rglob("*.strm")
        }
        self.assertEqual(before, after)
        self.assertEqual(
            set(disabled_summary), set(enabled_summary),
        )
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE media_type='movie'"
            ).fetchone()[0], 1)

    def test_enabled_and_disabled_authoritative_table_projections_match(self):
        path = self.playlist("table-parity.m3u", [
            ('cuid="parity" year="2024"', "Parity Movie",
             "https://example.invalid/parity"),
        ])
        original_data_dir = os.environ["MEDIA_ROUTER_DATA_DIR"]

        def capture(enabled: bool):
            isolated = Path(self.temp.name) / ("enabled-db" if enabled else "disabled-db")
            os.environ["MEDIA_ROUTER_DATA_DIR"] = str(isolated)
            os.environ["MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED"] = (
                "true" if enabled else "false"
            )
            get_settings.cache_clear()
            summary = import_paths(
                [str(path)], "Test", media_type_hint="movie",
                source_feed_id=str(uuid4()),
            )
            with sqlite3.connect(isolated / "media_router.db") as conn:
                projections = {
                    "catalog_items": conn.execute(
                        """SELECT internal_id,media_type,title,normalized_title,
                                  group_title,tvg_id,tvg_name,tvg_logo,tvg_chno,cuid,
                                  show_name,season_number,episode_number,episode_title,
                                  parent_internal_id,confidence,raw_title
                           FROM catalog_items ORDER BY internal_id"""
                    ).fetchall(),
                    "source_availability": conn.execute(
                        """SELECT catalog_internal_id,provider_id,account_id,external_id,
                                  location_ref,media_type,enabled,metadata_confidence,
                                  notes,raw_extinf
                           FROM source_availability ORDER BY catalog_internal_id,location_ref"""
                    ).fetchall(),
                    "catalog_sources": conn.execute(
                        """SELECT catalog_internal_id,media_type,source_name,source_url,
                                  cuid,tvg_id,raw_extinf
                           FROM catalog_sources ORDER BY catalog_internal_id,source_url"""
                    ).fetchall(),
                    "catalog_imports": conn.execute(
                        """SELECT job_id,source_name,file_path,status,summary_json
                           FROM catalog_imports ORDER BY id"""
                    ).fetchall(),
                }
            summary = {
                key: value for key, value in summary.items()
                if key not in {"duration_seconds", "last_import_time"}
            }
            return summary, projections

        try:
            disabled = capture(False)
            enabled = capture(True)
            self.assertEqual(disabled, enabled)
        finally:
            os.environ["MEDIA_ROUTER_DATA_DIR"] = original_data_dir
            os.environ.pop("MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED", None)
            get_settings.cache_clear()

    def test_live_m3u_is_unchanged_and_live_is_not_ledgered(self):
        live = self.playlist("live.m3u", [
            ('cuid="live-one" tvg-id="live.one"', "Live One",
             "https://example.invalid/live"),
        ])
        import_paths([str(live)], "Live", media_type_hint="live")
        output = Path(self.temp.name) / "live-output" / "live.m3u"
        update_live_m3u_settings(LiveM3uSettingsUpdate(
            output_file_path=str(output), generation_mode="Unlimited",
        ))
        generate_live_m3u_output("http://localhost:8088")
        before = output.read_bytes()
        self.enable_ledger()
        import_paths(
            [str(live)], "Live", media_type_hint="live",
            source_feed_id=str(uuid4()),
        )
        generate_live_m3u_output("http://localhost:8088")
        self.assertEqual(before, output.read_bytes())
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM source_import_runs"
            ).fetchone()[0], 0)

    def test_runtime_and_integration_modules_do_not_reference_ledger(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "app/services/broker.py", "app/services/runtime.py",
            "app/services/emby.py", "app/api/runtime.py",
        ):
            text = (root / relative).read_text()
            self.assertNotIn("source_entries", text)
            self.assertNotIn("source_entry_occurrences", text)


if __name__ == "__main__":
    unittest.main()
