import os
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.schemas.providers import AccountCreate, ProviderCreate
from app.services.broker import ensure_broker_schema, list_reservations, release_reservation
from app.services.catalog import ensure_schema
from app.services.playback_tickets import issue_playback_ticket
from app.services.providers import create_account, create_provider
from app.services.logs import list_logs


class PlaybackTicketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        os.environ["MEDIA_ROUTER_PLAYBACK_TICKET_SECRET"] = "test-secret-that-is-at-least-32-bytes-long"
        get_settings.cache_clear()
        ensure_schema()
        ensure_broker_schema()
        provider = create_provider(ProviderCreate(friendly_name="Provider"))
        self.accounts = [create_account(AccountCreate(provider_id=provider.id, friendly_name=f"Stream {n}", max_simultaneous_streams=1)) for n in (2, 3)]
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            for item in ("channel_one", "channel_two"):
                conn.execute("INSERT INTO catalog_items(internal_id,media_type,title,normalized_title,confidence,created_at,updated_at) VALUES (?,?,?,?,'high',?,?)", (item, "channel", item, item, now, now))
                for index, account in enumerate(self.accounts):
                    conn.execute("INSERT INTO source_availability(catalog_internal_id,provider_id,account_id,location_ref,media_type,enabled,last_seen_at,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?,?)", (item, provider.id, account.id, f"https://provider.invalid/live/credential/password/{index}", "channel", now, now, now))
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        os.environ.pop("MEDIA_ROUTER_PLAYBACK_TICKET_SECRET", None)
        self.temp.cleanup()

    def resolve(self):
        response = self.client.post("/api/broker/resolve", json={"catalog_item_id": "channel_one", "media_type": "channel", "client_session": "raw-emby-session"})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_ticketed_get_reuses_explicit_reservation_account_idempotently(self):
        decision = self.resolve()
        url = decision["runtime_url"]
        self.assertEqual(decision["stream_url"], url)
        self.assertTrue(urlsplit(url).query.startswith("ticket="))
        self.assertNotIn("raw-emby-session", url)
        self.assertNotIn("credential", url)
        path = urlsplit(url).path + "?" + urlsplit(url).query
        first = self.client.get(path)
        second = self.client.get(path, headers={"user-agent": "different-player"})
        self.assertEqual((first.status_code, second.status_code), (302, 302))
        self.assertEqual(first.headers["x-media-router-reservation-id"], decision["reservation"]["reservation_id"])
        self.assertEqual(first.headers["x-media-router-selected-account"], second.headers["x-media-router-selected-account"])
        reservations = list_reservations()
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0].identity_type, "explicit_session")
        self.assertEqual(reservations[0].account_id, decision["reservation"]["account_id"])
        messages = [row.message for row in list_logs()]
        self.assertTrue(any("reservation_ticket_validated" in message for message in messages))
        self.assertTrue(any("ticketed_reservation_reused" in message for message in messages))
        self.assertFalse(any("reservation_created identity_type=derived_fingerprint" in message for message in messages))

    def test_tampered_expired_mismatch_and_released_are_rejected_without_allocation(self):
        decision = self.resolve()
        parsed = urlsplit(decision["runtime_url"])
        path = parsed.path + "?" + parsed.query
        tampered = path[:-1] + ("A" if path[-1] != "A" else "B")
        self.assertEqual(self.client.get(tampered).status_code, 400)
        reservation_id = decision["reservation"]["reservation_id"]
        expired, _ = issue_playback_ticket(reservation_id, "channel_one", datetime.utcnow() - timedelta(seconds=2))
        self.assertEqual(self.client.get(f"/r/live/channel_one?ticket={expired}").status_code, 410)
        self.assertEqual(self.client.get(path.replace("channel_one", "channel_two", 1)).status_code, 409)
        release_reservation(reservation_id)
        self.assertEqual(self.client.get(path).status_code, 410)
        self.assertEqual(len(list_reservations()), 1)

    def test_unticketed_runtime_retains_generic_behavior(self):
        response = self.client.get("/r/live/channel_one", headers={"user-agent": "generic-player"})
        self.assertEqual(response.status_code, 302)
        reservation = list_reservations()[0]
        self.assertEqual(reservation.identity_type, "derived_fingerprint")


if __name__ == "__main__":
    unittest.main()
