import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import get_settings
from app.services import jobs
from app.services.playlist_refresh import (
    PlaylistRefreshScheduler, _legacy_vod_media_type, _restore_legacy_path,
    register_refresh_source, run_library_refresh_job,
)


class PlaylistRefreshSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        jobs.JOBS.clear()

    def tearDown(self):
        jobs.JOBS.clear()
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def _make_due(self) -> None:
        path = get_settings().data_dir / "playlist_refresh_sources.json"
        data = json.loads(path.read_text())
        for source in data["sources"]:
            source["last_success_at"] = "2000-01-01T00:00:00"
        path.write_text(json.dumps(data))

    def test_legacy_iptv_boss_vod_url_can_be_adopted(self):
        restored = _restore_legacy_path("http:/192.168.2.90:8080/VOD%20-%20Movies%203.m3u")
        self.assertEqual("http://192.168.2.90:8080/VOD%20-%20Movies%203.m3u", restored)
        self.assertEqual("movie", _legacy_vod_media_type(restored))
        self.assertIsNone(_legacy_vod_media_type("http://192.168.2.90:8080/THREAD3.m3u"))

    async def test_due_live_and_vod_sources_are_reimported(self):
        register_refresh_source(["live.m3u"], "Live", "p", "a", "live", None)
        register_refresh_source(["movies.m3u"], "Movies", "p", "a", "movie", "feed")
        self._make_due()
        calls = []

        def complete(job_id, paths, source_name, provider_id, account_id, media_type, source_feed_id):
            calls.append((paths, media_type))
            jobs.update_job(job_id, status="complete", progress=100, message="Complete")

        with patch("app.services.playlist_refresh.run_catalog_import_job", side_effect=complete):
            completed = await PlaylistRefreshScheduler().run_due_once()

        self.assertEqual(2, completed)
        self.assertEqual([(["live.m3u"], "live"), (["movies.m3u"], "movie")], calls)

    async def test_recent_sources_and_unscoped_imports_are_not_run(self):
        register_refresh_source(["live.m3u"], "Live", None, None, "live", None)
        register_refresh_source(["unknown.m3u"], "Unknown", None, None, None, None)

        with patch("app.services.playlist_refresh.run_catalog_import_job") as run:
            completed = await PlaylistRefreshScheduler().run_due_once()

        self.assertEqual(0, completed)
        run.assert_not_called()
        data = json.loads((get_settings().data_dir / "playlist_refresh_sources.json").read_text())
        self.assertEqual(1, len(data["sources"]))

    async def test_active_catalog_import_defers_scheduled_work(self):
        register_refresh_source(["live.m3u"], "Live", None, None, "live", None)
        self._make_due()
        jobs.create_job("catalog_import")

        with patch("app.services.playlist_refresh.run_catalog_import_job") as run:
            completed = await PlaylistRefreshScheduler().run_due_once()

        self.assertEqual(0, completed)
        run.assert_not_called()

    def test_manual_library_refresh_forces_all_sources_sequentially(self):
        register_refresh_source(["live.m3u"], "Live", "p", "a", "live", None)
        register_refresh_source(["movies.m3u"], "Movies", "p", "a", "movie", None)
        job = jobs.create_job("library_refresh")
        calls = []

        with patch(
            "app.services.playlist_refresh.import_paths",
            side_effect=lambda paths, *args: calls.append(paths),
        ):
            run_library_refresh_job(job.id)

        result = jobs.get_job(job.id)
        self.assertEqual("complete", result.status)
        self.assertEqual({"sources": 2, "completed": 2, "failed": 0}, result.result)
        self.assertEqual([["live.m3u"], ["movies.m3u"]], calls)


if __name__ == "__main__":
    unittest.main()
