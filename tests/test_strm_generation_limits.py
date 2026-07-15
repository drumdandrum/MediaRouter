import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import json
from datetime import datetime
from unittest.mock import patch

from app.core.config import get_settings
from app.schemas.outputs import StrmSettingsUpdate
from app.services.catalog import ensure_schema, import_paths, list_channel_placements
from app.services.outputs import (_atomic_write_strm, _db_path, dry_run_strm_outputs, generate_strm_outputs, generate_live_m3u_output,
    dry_run_live_m3u_output, get_live_m3u_settings, update_live_m3u_settings, update_strm_settings)
from app.schemas.outputs import LiveM3uSettingsUpdate


class StrmGenerationLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        ensure_schema()
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(_db_path()) as conn:
            rows = []
            for kind in ("movie", "episode"):
                for index in range(7):
                    rows.append((f"{kind}_{index}", kind, f"{kind} {index}", f"{kind} {index}", "high", now, now))
            conn.executemany("""INSERT INTO catalog_items
                (internal_id, media_type, title, normalized_title, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""", rows)
        output = Path(self.temp.name) / "outputs"
        (output / "movies").mkdir(parents=True)
        (output / "series").mkdir()
        self.output = output

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def test_defaults_preserve_500_limits(self):
        settings = update_strm_settings(StrmSettingsUpdate(
            movies_output_directory=str(self.output / "movies"),
            series_output_directory=str(self.output / "series"),
        ))
        self.assertEqual((settings.generation_mode, settings.maximum_movies, settings.maximum_episodes), ("Test", 500, 500))

    def test_custom_limits_and_batches_apply_to_dry_run_without_writes(self):
        update_strm_settings(StrmSettingsUpdate(
            movies_output_directory=str(self.output / "movies"),
            series_output_directory=str(self.output / "series"),
            generation_mode="Custom", maximum_movies=3, maximum_episodes=4, batch_size=50,
        ))
        result = dry_run_strm_outputs("http://localhost:8088")
        self.assertEqual((result.summary.movie_count, result.summary.episode_count), (3, 4))
        self.assertEqual(result.summary.excluded_by_limits, 7)
        self.assertFalse(list(self.output.rglob("*.strm")))

    def test_presets_are_enforced_and_invalid_custom_is_rejected(self):
        for mode, expected in (("Small", (2000, 2000)), ("Medium", (5000, 10000))):
            settings = update_strm_settings(StrmSettingsUpdate(generation_mode=mode, maximum_movies=1, maximum_episodes=1))
            self.assertEqual((settings.maximum_movies, settings.maximum_episodes), expected)
        with self.assertRaises(ValueError):
            update_strm_settings(StrmSettingsUpdate(generation_mode="Custom", maximum_movies=0, maximum_episodes=1))

    def test_generate_is_atomic_idempotent_and_reports_benchmark_metrics(self):
        update_strm_settings(StrmSettingsUpdate(
            movies_output_directory=str(self.output / "movies"), series_output_directory=str(self.output / "series"),
            generation_mode="Custom", maximum_movies=7, maximum_episodes=7, batch_size=50, worker_count=4,
        ))
        generated = generate_strm_outputs("http://localhost:8088")
        files = list(self.output.rglob("*.strm"))
        self.assertEqual(len(files), 14)
        self.assertTrue(all(path.read_text().startswith("http://localhost:8088/r/") for path in files))
        self.assertEqual(list(self.output.rglob("*.tmp")), [])
        self.assertGreater(generated.summary.items_per_second, 0)
        self.assertGreater(generated.summary.average_ms_per_item, 0)
        rerun = generate_strm_outputs("http://localhost:8088")
        self.assertEqual((rerun.summary.created_count, rerun.summary.updated_count, rerun.summary.skipped_count), (0, 0, 14))
        files[0].write_text("externally changed\n")
        repaired = generate_strm_outputs("http://localhost:8088")
        self.assertEqual((repaired.summary.updated_count, repaired.summary.skipped_count), (1, 13))

    def test_bounded_workers_improve_delayed_file_work(self):
        original = _atomic_write_strm

        def delayed_write(path, content):
            time.sleep(0.02)
            original(path, content)

        durations = []
        with patch("app.services.outputs._atomic_write_strm", side_effect=delayed_write):
            for workers, suffix in ((1, "serial"), (4, "parallel")):
                movies = self.output / suffix / "movies"
                series = self.output / suffix / "series"
                movies.mkdir(parents=True)
                series.mkdir()
                update_strm_settings(StrmSettingsUpdate(
                    movies_output_directory=str(movies), series_output_directory=str(series),
                    generation_mode="Custom", maximum_movies=7, maximum_episodes=7,
                    batch_size=50, worker_count=workers,
                ))
                started = time.monotonic()
                result = generate_strm_outputs("http://localhost:8088")
                durations.append(time.monotonic() - started)
                self.assertEqual(result.summary.failed_count, 0)
        self.assertLess(durations[1], durations[0] * 0.7)


class LiveM3uGenerationLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        ensure_schema()
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(_db_path()) as conn:
            for index in range(7):
                internal_id = f"channel_{index}"
                conn.execute("""INSERT INTO catalog_items
                    (internal_id,media_type,title,normalized_title,group_title,tvg_id,tvg_name,tvg_logo,tvg_chno,confidence,created_at,updated_at)
                    VALUES (?, 'channel', ?, ?, 'News', ?, ?, ?, ?, 'high', ?, ?)""",
                    (internal_id, f"Channel {index}", f"channel {index}", f"tvg-{index}", f"TVG {index}",
                     f"https://logos.invalid/{index}.png", str(7-index), now, now))
                if index != 6:
                    conn.execute("""INSERT INTO source_availability
                        (catalog_internal_id,location_ref,media_type,enabled,last_seen_at,created_at,updated_at)
                        VALUES (?, ?, 'channel', 1, ?, ?, ?)""", (internal_id, "https://provider.invalid/secret", now, now, now))
        self.output = Path(self.temp.name) / "live" / "live.m3u"

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def test_existing_settings_default_to_safe_test_limit(self):
        settings = get_live_m3u_settings()
        self.assertEqual((settings.generation_mode, settings.maximum_live_channels), ("Test", 500))

    def test_legacy_positive_channel_limit_migrates_as_custom(self):
        path = get_settings().data_dir / "outputs_live_m3u_settings.json"
        path.write_text(json.dumps({"output_file_path": str(self.output), "channel_limit": 1234}))
        settings = get_live_m3u_settings()
        self.assertEqual((settings.generation_mode, settings.maximum_live_channels), ("Custom", 1234))

    def test_custom_limit_applies_to_dry_run_and_generate_without_provider_urls(self):
        update_live_m3u_settings(LiveM3uSettingsUpdate(output_file_path=str(self.output),
            generation_mode="Custom", maximum_live_channels=3))
        dry = dry_run_live_m3u_output("http://localhost:8088")
        self.assertFalse(self.output.exists())
        self.assertEqual((dry.summary.eligible_live_channels, dry.summary.included_channels, dry.summary.excluded_by_limit), (6, 3, 3))
        generated = generate_live_m3u_output("http://localhost:8088")
        content = self.output.read_text()
        self.assertEqual(generated.summary.written_count, 3)
        self.assertEqual(content.count("#EXTINF"), 3)
        self.assertIn("/r/live/channel_", content)
        self.assertNotIn("provider.invalid", content)
        self.assertLess(content.index('tvg-chno="5"'), content.index('tvg-chno="6"'))
        rerun = generate_live_m3u_output("http://localhost:8088")
        self.assertEqual((rerun.summary.created_count, rerun.summary.updated_count), (0, 0))

    def test_presets_and_custom_validation(self):
        for mode, expected in (("Test", 500), ("Small", 2000), ("Medium", 5000)):
            settings = update_live_m3u_settings(LiveM3uSettingsUpdate(generation_mode=mode, maximum_live_channels=1))
            self.assertEqual(settings.maximum_live_channels, expected)
        with self.assertRaises(ValueError):
            update_live_m3u_settings(LiveM3uSettingsUpdate(generation_mode="Custom", maximum_live_channels=0))

    def test_import_preserves_multiple_editorial_placements_without_duplicate_identity(self):
        playlist = Path(self.temp.name) / "THREAD1.m3u"
        playlist.write_text("""#EXTM3U
#EXTINF:-1 cuid="shared-cuid" tvg-chno="1" group-title="LA Locals & Favorites",HGTV WEST HD
https://provider.invalid/live/one
#EXTINF:-1 cuid="shared-cuid" tvg-chno="1977" group-title="Cable TV Channels",HGTV WEST HD
https://provider.invalid/live/one
""")
        import_paths([str(playlist)], "THREAD1", media_type_hint="live")
        with sqlite3.connect(_db_path()) as conn:
            identity_count = conn.execute("SELECT COUNT(*) FROM catalog_items WHERE cuid='shared-cuid'").fetchone()[0]
            internal_id = conn.execute("SELECT internal_id FROM catalog_items WHERE cuid='shared-cuid'").fetchone()[0]
        self.assertEqual(identity_count, 1)
        placements = [p for p in list_channel_placements(internal_id) if p.source_identity != "legacy-canonical"]
        self.assertEqual([(p.group_title, p.channel_number) for p in placements],
            [("LA Locals & Favorites", "1"), ("Cable TV Channels", "1977")])
        import_paths([str(playlist)], "THREAD1", media_type_hint="live")
        self.assertEqual(len([p for p in list_channel_placements(internal_id) if p.source_identity != "legacy-canonical"]), 2)
        update_live_m3u_settings(LiveM3uSettingsUpdate(output_file_path=str(self.output), generation_mode="Unlimited"))
        generate_live_m3u_output("http://localhost:8088")
        content = self.output.read_text()
        self.assertEqual(content.count(f"/r/live/{internal_id}"), 2)
        self.assertLess(content.index('group-title="LA Locals &amp; Favorites"'), content.index('group-title="Cable TV Channels"'))
        playlist.write_text("""#EXTM3U
#EXTINF:-1 cuid="shared-cuid" tvg-chno="1" group-title="LA Locals & Favorites",HGTV WEST HD
https://provider.invalid/live/one
""")
        import_paths([str(playlist)], "THREAD1", media_type_hint="live")
        self.assertEqual(len(list_channel_placements(internal_id)), 1)


if __name__ == "__main__":
    unittest.main()
