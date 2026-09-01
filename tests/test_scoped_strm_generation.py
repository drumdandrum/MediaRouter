import os
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from app.db.migrations import migrate_database
from app.schemas.outputs import StrmGenerateRequest, StrmSettingsUpdate
from app.services.jobs import JOBS, create_job, get_job
from app.services.outputs import (
    _db_path,
    generate_strm_outputs,
    list_output_history,
    run_strm_generate_job,
    update_strm_settings,
    validate_strm_catalog_item_ids,
)


class ScopedStrmGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        JOBS.clear()
        migrate_database()
        self.output = Path(self.temp.name) / "outputs"
        self.movies = self.output / "movies"
        self.series = self.output / "series"
        self.movies.mkdir(parents=True)
        self.series.mkdir()
        update_strm_settings(StrmSettingsUpdate(
            movies_output_directory=str(self.movies),
            series_output_directory=str(self.series),
            generation_mode="Custom",
            maximum_movies=100,
            maximum_episodes=100,
            remove_orphaned_files=True,
        ))
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(_db_path()) as conn:
            for index in range(9):
                item_id = f"movie_existing_{index}"
                title = f"Existing Movie {index}"
                conn.execute(
                    """INSERT INTO catalog_items
                    (internal_id,media_type,title,normalized_title,confidence,created_at,updated_at)
                    VALUES (?, 'movie', ?, ?, 'high', ?, ?)""",
                    (item_id, title, title.casefold(), now, now),
                )
                path = self.movies / f"{title}.strm"
                path.write_text(f"http://localhost:8088/r/movie/{item_id}\n")
                conn.execute(
                    """INSERT INTO output_generated_files
                    (output_id,catalog_item_id,media_type,output_type,output_path,last_content_hash,
                     last_generated_at,status,generation_run_id)
                    VALUES ('strm',?,'movie','strm',?,'protected',?,'generated','old-run')""",
                    (item_id, str(path), now),
                )
            conn.execute(
                """INSERT INTO catalog_items
                (internal_id,media_type,title,normalized_title,confidence,created_at,updated_at)
                VALUES ('movie_playable','movie','MediaRouter Playback Test','mediarouter playback test','high',?,?)""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO source_availability
                (catalog_internal_id,provider_id,account_id,external_id,location_ref,media_type,enabled,
                 last_seen_at,created_at,updated_at)
                VALUES ('movie_playable','provider-lab','account-lab','lab-playback-movie-001',
                        'http://host.docker.internal:18091/playback-test.mp4','movie',1,?,?,?)""",
                (now, now, now),
            )
            conn.execute(
                """INSERT INTO catalog_items
                (internal_id,media_type,title,normalized_title,show_name,season_number,episode_number,
                 episode_title,confidence,created_at,updated_at)
                VALUES ('episode_playable','episode','Fixture Episode','fixture episode','Fixture Show',1,1,
                        'Fixture Episode','high',?,?)""",
                (now, now),
            )
            for item_id, media_type in (("series_invalid", "series"), ("channel_invalid", "channel")):
                conn.execute(
                    """INSERT INTO catalog_items
                    (internal_id,media_type,title,normalized_title,confidence,created_at,updated_at)
                    VALUES (?,?,?,?, 'high',?,?)""",
                    (item_id, media_type, item_id, item_id, now, now),
                )

    def tearDown(self):
        JOBS.clear()
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def _protected_snapshot(self):
        files = {}
        for path in sorted(self.movies.glob("Existing Movie *.strm")):
            stat = path.stat()
            files[path.name] = (path.read_bytes(), stat.st_mode, stat.st_mtime_ns, stat.st_ino)
        with sqlite3.connect(_db_path()) as conn:
            tracking = conn.execute(
                """SELECT catalog_item_id,output_path,last_content_hash,last_generated_at,status,generation_run_id
                FROM output_generated_files WHERE catalog_item_id LIKE 'movie_existing_%'
                ORDER BY catalog_item_id"""
            ).fetchall()
        return files, tracking

    def _protected_digest(self):
        digest = hashlib.sha256()
        for path in sorted(self.movies.glob("Existing Movie *.strm")):
            digest.update(path.name.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_request_contract_rejects_empty_duplicate_large_and_malformed_scopes(self):
        self.assertIsNone(StrmGenerateRequest().catalog_item_ids)
        self.assertIsNone(StrmGenerateRequest(catalog_item_ids=None).catalog_item_ids)
        for value in ([], ["movie_playable", "movie_playable"], [f"movie_{i}" for i in range(101)],
                      ["x" * 129], ["../movie_playable"]):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    StrmGenerateRequest(catalog_item_ids=value)

    def test_scope_resolution_rejects_missing_series_and_live_items(self):
        before = self._protected_snapshot()
        for item_id in ("missing_item", "series_invalid", "channel_invalid"):
            with self.subTest(item_id=item_id):
                with self.assertRaisesRegex(ValueError, item_id):
                    generate_strm_outputs("http://localhost:8088", [item_id])
                self.assertEqual(before, self._protected_snapshot())

    def test_service_rejects_scope_shape_before_output_mutation(self):
        before = self._protected_snapshot()
        for value in ([], ["movie_playable", "movie_playable"], ["../movie_playable"]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    generate_strm_outputs("http://localhost:8088", value)
                self.assertEqual(before, self._protected_snapshot())

    def test_api_passes_valid_scope_and_rejects_invalid_scope_before_job_creation(self):
        with patch("app.api.outputs.run_strm_generate_job") as run:
            response = TestClient(app).post(
                "/api/outputs/strm/generate",
                json={"catalog_item_ids": ["movie_playable"], "confirm_unlimited": False},
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(run.call_args.args[2], ["movie_playable"])
        before_jobs = len(JOBS)
        response = TestClient(app).post(
            "/api/outputs/strm/generate",
            json={"catalog_item_ids": ["missing_item"], "confirm_unlimited": False},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(JOBS), before_jobs)

    def test_empty_api_body_remains_global_and_openapi_exposes_optional_scope(self):
        with patch("app.api.outputs.run_strm_generate_job") as run:
            response = TestClient(app).post("/api/outputs/strm/generate", json={})
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(run.call_args.args[2])
        schema = app.openapi()["components"]["schemas"]["StrmGenerateRequest"]
        self.assertIn("catalog_item_ids", schema["properties"])
        self.assertNotIn("catalog_item_ids", schema.get("required", []))

    def test_one_movie_changes_only_selected_output_and_tracking(self):
        before = self._protected_snapshot()
        before_digest = self._protected_digest()
        result = generate_strm_outputs("http://localhost:8088", ["movie_playable"])
        self.assertEqual((result.summary.created_count, result.summary.movie_count,
                          result.summary.episode_count), (1, 1, 0))
        self.assertEqual(result.summary.scope_mode, "catalog_items")
        self.assertEqual(result.summary.requested_catalog_item_ids, ["movie_playable"])
        self.assertTrue(result.summary.orphan_cleanup_skipped_due_to_scope)
        self.assertFalse(result.summary.orphan_cleanup_performed)
        self.assertEqual(before, self._protected_snapshot())
        self.assertEqual(before_digest, self._protected_digest())
        content = (self.movies / "MediaRouter Playback Test.strm").read_text()
        self.assertEqual(content, "http://localhost:8088/r/movie/movie_playable\n")
        self.assertNotIn("host.docker.internal:18091", content)
        with sqlite3.connect(_db_path()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM output_generated_files WHERE catalog_item_id='movie_playable'"
            ).fetchone()[0], 1)

    def test_selected_existing_file_updates_under_existing_overwrite_policy(self):
        generate_strm_outputs("http://localhost:8088", ["movie_playable"])
        target = self.movies / "MediaRouter Playback Test.strm"
        target.write_text("selected external change\n")
        before = self._protected_snapshot()
        result = generate_strm_outputs("http://localhost:8088", ["movie_playable"])
        self.assertEqual((result.summary.updated_count, result.summary.skipped_count), (1, 0))
        self.assertEqual(target.read_text(), "http://localhost:8088/r/movie/movie_playable\n")
        self.assertEqual(before, self._protected_snapshot())

    def test_scoped_run_never_stats_or_reads_unselected_files(self):
        protected = set(self.movies.glob("Existing Movie *.strm"))
        original_stat = Path.stat
        original_read_bytes = Path.read_bytes

        def guarded_stat(path, *args, **kwargs):
            if path in protected:
                raise AssertionError(f"scoped run stat-ed unselected file {path.name}")
            return original_stat(path, *args, **kwargs)

        def guarded_read(path, *args, **kwargs):
            if path in protected:
                raise AssertionError(f"scoped run read unselected file {path.name}")
            return original_read_bytes(path, *args, **kwargs)

        with patch.object(Path, "stat", guarded_stat), patch.object(Path, "read_bytes", guarded_read):
            result = generate_strm_outputs("http://localhost:8088", ["movie_playable"])
        self.assertEqual(result.summary.created_count, 1)

    def test_valid_episode_and_mixed_scope_are_supported(self):
        episode = generate_strm_outputs("http://localhost:8088", ["episode_playable"])
        self.assertEqual((episode.summary.movie_count, episode.summary.episode_count), (0, 1))
        mixed = generate_strm_outputs(
            "http://localhost:8088", ["movie_playable", "episode_playable"]
        )
        self.assertEqual((mixed.summary.movie_count, mixed.summary.episode_count), (1, 1))
        self.assertEqual(mixed.summary.requested_catalog_item_count, 2)

    def test_scoped_run_skips_orphan_cleanup_but_global_run_retains_it(self):
        orphan = self.movies / "Old Playable Name.strm"
        orphan.write_text("old\n")
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                """INSERT INTO output_generated_files
                (output_id,catalog_item_id,media_type,output_type,output_path,last_content_hash,
                 last_generated_at,status,generation_run_id)
                VALUES ('strm','movie_playable','movie','strm',?,'old',?,'generated','old-run')""",
                (str(orphan), now),
            )
        scoped = generate_strm_outputs("http://localhost:8088", ["episode_playable"])
        self.assertTrue(orphan.exists())
        self.assertTrue(scoped.summary.orphan_cleanup_skipped_due_to_scope)
        global_result = generate_strm_outputs("http://localhost:8088")
        self.assertFalse(orphan.exists())
        self.assertTrue(global_result.summary.orphan_cleanup_performed)

    def test_scoped_collision_does_not_overwrite_unselected_owner(self):
        protected = self.movies / "MediaRouter Playback Test.strm"
        protected.write_text("unselected owner\n")
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "UPDATE catalog_items SET title='MediaRouter Playback Test' WHERE internal_id='movie_existing_0'"
            )
            conn.execute(
                "UPDATE output_generated_files SET output_path=? WHERE catalog_item_id='movie_existing_0'",
                (str(protected),),
            )
        result = generate_strm_outputs("http://localhost:8088", ["movie_playable"])
        self.assertEqual(protected.read_text(), "unselected owner\n")
        self.assertEqual(result.operations[0].output_path,
                         str(self.movies / "MediaRouter Playback Test [movie_playable].strm"))

    def test_selected_write_and_tracking_failures_leave_unrelated_state_unchanged(self):
        before = self._protected_snapshot()
        with patch("app.services.outputs._atomic_write_strm", side_effect=OSError("synthetic write failure")):
            result = generate_strm_outputs("http://localhost:8088", ["movie_playable"])
        self.assertEqual(result.summary.failed_count, 1)
        self.assertEqual(before, self._protected_snapshot())
        with patch("app.services.outputs._record_generated_files", side_effect=sqlite3.OperationalError("synthetic tracking failure")):
            with self.assertRaises(sqlite3.OperationalError):
                generate_strm_outputs("http://localhost:8088", ["episode_playable"])
        self.assertEqual(before, self._protected_snapshot())

    def test_history_and_job_results_record_bounded_scope(self):
        job = create_job("strm_generate")
        run_strm_generate_job(job.id, "http://localhost:8088", ["movie_playable"])
        completed = get_job(job.id)
        self.assertEqual(completed.status, "complete")
        self.assertEqual(completed.result["scope_mode"], "catalog_items")
        self.assertEqual(completed.result["requested_catalog_item_ids"], ["movie_playable"])
        history = list_output_history()[0]
        self.assertEqual(history.summary.scope_mode, "catalog_items")
        self.assertEqual(history.summary.requested_catalog_item_count, 1)

    def test_global_scope_defaults_and_selection_remain_global(self):
        self.assertIsNone(validate_strm_catalog_item_ids(None))
        result = generate_strm_outputs("http://localhost:8088")
        self.assertEqual(result.summary.scope_mode, "global")
        self.assertEqual(result.summary.requested_catalog_item_count, 0)
        self.assertEqual((result.summary.movie_count, result.summary.episode_count), (10, 1))


if __name__ == "__main__":
    unittest.main()
