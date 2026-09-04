import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.migrations import migrate_database
from app.main import app
from app.schemas.outputs import LiveM3uSettingsUpdate
from app.schemas.settings import SettingsUpdate
from app.services.broker import list_reservations
from app.services.logs import LOGS, list_logs
from app.services.outputs import (
    _db_path,
    build_live_m3u_document,
    generate_live_m3u_output,
    update_live_m3u_settings,
)
from app.services.settings import update_app_settings


class _TrackingConnection(sqlite3.Connection):
    live_count = 0
    statements = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).live_count += 1
        self._closed_once = False

    def close(self):
        if not self._closed_once:
            self._closed_once = True
            type(self).live_count -= 1
        return super().close()

    def execute(self, sql, parameters=()):
        type(self).statements.append(sql.strip())
        return super().execute(sql, parameters)


class NativeLiveM3uTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        LOGS.clear()
        migrate_database()
        update_app_settings(SettingsUpdate(runtime_public_base_url="http://router.test:8088"))
        update_live_m3u_settings(LiveM3uSettingsUpdate(
            output_file_path=str(Path(self.temp.name) / "live" / "live.m3u"),
            generation_mode="Unlimited",
        ))
        self._insert_fixture()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        LOGS.clear()
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def _insert_fixture(self):
        now = "2026-09-04T00:00:00"
        with sqlite3.connect(_db_path()) as conn:
            channels = (
                ("channel_unicode", "Café 東京", "cafe", "News & Talk", "guide&one", 'Café "HD"', None, "2"),
                ("channel_first", "First", "first", "General", "guide-first", "First", "https://logos.test/first.png", "1"),
                ("channel_disabled", "Disabled", "disabled", None, None, None, None, "3"),
            )
            conn.executemany("""INSERT INTO catalog_items
                (internal_id,media_type,title,normalized_title,group_title,tvg_id,tvg_name,tvg_logo,tvg_chno,
                 confidence,created_at,updated_at)
                VALUES (?, 'channel', ?, ?, ?, ?, ?, ?, ?, 'high', ?, ?)""",
                [(*row, now, now) for row in channels])
            conn.executemany("""INSERT INTO source_availability
                (catalog_internal_id,location_ref,media_type,enabled,last_seen_at,created_at,updated_at)
                VALUES (?, ?, 'channel', ?, ?, ?, ?)""", (
                ("channel_unicode", "https://user:secret@provider.invalid/live/one?token=hidden", 1, now, now, now),
                ("channel_first", "https://provider.invalid/live/two", 1, now, now, now),
                ("channel_disabled", "https://provider.invalid/live/three", 0, now, now, now),
            ))
            conn.executemany("""INSERT INTO channel_placements
                (catalog_item_id,source_identity,source_name,source_playlist,group_title,channel_number,
                 display_title,placement_index,tvg_id,tvg_name,tvg_logo,active,created_at,updated_at)
                VALUES (?, ?, 'fixture', 'fixture.m3u', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""", (
                ("channel_unicode", "fixture", "News & Talk", "2", "Café 東京\nInjected", 1,
                 "guide&one", 'Café "HD"', None, now, now),
                ("channel_first", "fixture", "General", "1", "First", 0,
                 "guide-first", "First", "https://logos.test/first.png", now, now),
                ("channel_disabled", "fixture", None, "3", "Disabled", 2,
                 None, None, None, now, now),
            ))

    def test_endpoint_is_deterministic_extended_m3u_without_capacity_or_provider_access(self):
        with patch("app.services.live_gateway._open_upstream") as upstream:
            first = self.client.get("/live/playlist.m3u")
            second = self.client.get("/live/playlist.m3u")

        self.assertEqual(200, first.status_code)
        self.assertEqual("application/x-mpegurl; charset=utf-8", first.headers["content-type"].lower())
        self.assertTrue(first.content.startswith(b"#EXTM3U\n"))
        self.assertEqual(first.content, second.content)
        self.assertEqual(first.headers["etag"], second.headers["etag"])
        self.assertEqual(2, first.text.count("#EXTINF:"))
        self.assertLess(first.text.index("channel_first"), first.text.index("channel_unicode"))
        self.assertIn('media-router-id="channel_unicode"', first.text)
        self.assertIn('tvg-id="guide&amp;one"', first.text)
        self.assertIn('tvg-name="Café &quot;HD&quot;"', first.text)
        self.assertIn('group-title="News &amp; Talk"', first.text)
        self.assertIn("Café 東京 Injected", first.text)
        self.assertIn("http://router.test:8088/r/live/channel_unicode?mr_catalog_id=channel_unicode", first.text)
        self.assertNotIn("provider.invalid", first.text)
        self.assertNotIn("secret", first.text)
        self.assertNotIn("token=", first.text)
        self.assertNotIn("channel_disabled", first.text)
        self.assertEqual([], list_reservations())
        upstream.assert_not_called()

    def test_missing_optional_metadata_and_empty_catalog_are_valid(self):
        response = self.client.get("/live/playlist.m3u")
        unicode_line = next(line for line in response.text.splitlines() if "channel_unicode" in line)
        self.assertNotIn("tvg-logo=", unicode_line)
        with sqlite3.connect(_db_path()) as conn:
            conn.execute("DELETE FROM source_availability")
            conn.execute("DELETE FROM channel_placements")
            conn.execute("DELETE FROM catalog_items")
        empty = self.client.get("/live/playlist.m3u")
        self.assertEqual(200, empty.status_code)
        self.assertEqual("#EXTM3U\n", empty.text)

    def test_disk_and_native_outputs_share_exact_canonical_content(self):
        native = self.client.get("/live/playlist.m3u")
        generated = generate_live_m3u_output()
        disk = Path(generated.summary.output_path).read_bytes()
        self.assertEqual(native.content, disk)
        self.assertEqual(build_live_m3u_document().digest, native.headers["etag"].strip('"'))

    def test_request_host_and_forwarding_headers_never_control_gateway_urls(self):
        response = self.client.get("/live/playlist.m3u", headers={
            "host": "attacker.invalid",
            "x-forwarded-host": "forwarded.invalid",
            "x-forwarded-proto": "https",
        })
        self.assertIn("http://router.test:8088/r/live/", response.text)
        self.assertNotIn("attacker.invalid", response.text)
        self.assertNotIn("forwarded.invalid", response.text)

        update_app_settings(SettingsUpdate(runtime_public_base_url="", public_base_url="http://media-router:8088"))
        fallback = self.client.get("/live/playlist.m3u", headers={"host": "attacker.invalid"})
        self.assertIn("http://localhost:8088/r/live/", fallback.text)
        self.assertNotIn("attacker.invalid", fallback.text)

    def test_invalid_base_and_database_errors_are_redacted(self):
        update_app_settings(SettingsUpdate(runtime_public_base_url="https://user:password@router.test"))
        invalid = self.client.get("/live/playlist.m3u")
        self.assertEqual(503, invalid.status_code)
        self.assertNotIn("password", invalid.text)

        with patch("app.api.distribution.build_live_m3u_document",
                   side_effect=sqlite3.OperationalError(
                       "SELECT secret FROM db https://user:password@provider.invalid/watch?token=hidden")):
            failed = self.client.get("/live/playlist.m3u")
        self.assertEqual(503, failed.status_code)
        serialized = failed.text + " ".join(entry.message for entry in list_logs())
        for secret in ("SELECT", "password", "provider.invalid", "hidden"):
            self.assertNotIn(secret, serialized)

    def test_document_read_closes_its_database_connection(self):
        real_connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            return real_connect(*args, factory=_TrackingConnection, **kwargs)

        _TrackingConnection.live_count = 0
        _TrackingConnection.statements = []
        with patch("app.services.outputs.sqlite3.connect", side_effect=tracked_connect):
            build_live_m3u_document()
        self.assertEqual(0, _TrackingConnection.live_count)
        self.assertLessEqual(len(_TrackingConnection.statements), 4)
        self.assertTrue(all(statement.upper().startswith(("PRAGMA", "SELECT"))
                            for statement in _TrackingConnection.statements))


if __name__ == "__main__":
    unittest.main()
