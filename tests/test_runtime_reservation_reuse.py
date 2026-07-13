import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from fastapi.testclient import TestClient
from app.main import app

from app.core.config import get_settings
from app.schemas.providers import AccountCreate, ProviderCreate
from app.services.broker import BrokerUnavailable, ensure_broker_schema, list_reservations, repair_duplicate_reservations, resolve_source
from app.services.catalog import ensure_schema
from app.services.providers import create_account, create_provider
from app.services.runtime import runtime_client_fingerprint


class RuntimeReservationReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        ensure_schema()
        ensure_broker_schema()
        provider = create_provider(ProviderCreate(friendly_name="Provider"))
        self.accounts = [
            create_account(AccountCreate(provider_id=provider.id, friendly_name="Stream 1", max_simultaneous_streams=1, weight=100)),
            create_account(AccountCreate(provider_id=provider.id, friendly_name="Stream 2", max_simultaneous_streams=1, weight=90)),
        ]
        now = datetime.utcnow().isoformat()
        db = get_settings().data_dir / "media_router.db"
        with sqlite3.connect(db) as conn:
            for catalog_id, media_type in (("movie_one", "movie"), ("movie_two", "movie"), ("live_one", "channel")):
                conn.execute("""INSERT INTO catalog_items
                    (internal_id,media_type,title,normalized_title,confidence,created_at,updated_at)
                    VALUES (?,?,?,?, 'high',?,?)""", (catalog_id, media_type, catalog_id, catalog_id, now, now))
                for index, account in enumerate(self.accounts):
                    conn.execute("""INSERT INTO source_availability
                        (catalog_internal_id,provider_id,account_id,location_ref,media_type,enabled,last_seen_at,created_at,updated_at)
                        VALUES (?,?,?,?,?,1,?,?,?)""", (catalog_id, provider.id, account.id,
                        f"https://provider.invalid/{media_type}/secret/secret/{index}", media_type, now, now, now))

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def resolve(self, catalog_id="movie_one", media_type="movie", fingerprint="client-a", reserve=True):
        return resolve_source(catalog_id, media_type, client_fingerprint=fingerprint,
            allow_reservation_reuse=True, reserve=reserve, reservation_ttl_seconds=3600)

    def test_fingerprint_ignores_ports_and_minor_user_agent_versions(self):
        first = runtime_client_fingerprint("movie_one", "movie", "192.0.2.10:41000", "Emby/4.8.1 ffmpeg/6.0")
        second = runtime_client_fingerprint("movie_one", "movie", "192.0.2.10:52000", "  emby/4.8.2   ffmpeg/6.1 ")
        self.assertEqual(first, second)

    def test_get_head_range_and_reconnect_reuse_one_active_reservation(self):
        decisions = [self.resolve() for _ in range(4)]
        head = self.resolve(reserve=False)
        ranged_seek = self.resolve()
        ids = {decision.reservation.reservation_id for decision in [*decisions, head, ranged_seek]}
        self.assertEqual(len(ids), 1)
        active = [row for row in list_reservations() if row.status == "active"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].reuse_count, 5)

    def test_http_head_get_and_range_requests_share_runtime_reservation(self):
        client = TestClient(app, follow_redirects=False)
        head = client.head("/r/movie/movie_one", headers={"x-forwarded-for": "192.0.2.44:41000", "user-agent": "Emby/4.8.1"})
        first = client.get("/r/movie/movie_one", headers={"x-forwarded-for": "192.0.2.44:42000", "user-agent": "Emby/4.8.2", "range": "bytes=0-1023"})
        seek = client.get("/r/movie/movie_one", headers={"x-forwarded-for": "192.0.2.44:43000", "user-agent": "Emby/4.8.3", "range": "bytes=500000-"})
        self.assertEqual((head.status_code, first.status_code, seek.status_code), (302, 302, 302))
        self.assertEqual(first.headers["x-media-router-reservation-id"], seek.headers["x-media-router-reservation-id"])
        self.assertEqual(len([row for row in list_reservations() if row.status == "active"]), 1)

    def test_simultaneous_requests_are_atomic(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(pool.map(lambda _: self.resolve(fingerprint="simultaneous"), range(8)))
        self.assertEqual(len({decision.reservation.reservation_id for decision in decisions}), 1)
        self.assertEqual(len([row for row in list_reservations() if row.status == "active"]), 1)

    def test_active_identity_reuses_beyond_old_thirty_second_window(self):
        first = self.resolve(fingerprint="long-playback")
        old = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE broker_reservations SET created_at=? WHERE reservation_id=?", (old, first.reservation.reservation_id))
        second = self.resolve(fingerprint="long-playback")
        self.assertEqual(first.reservation.reservation_id, second.reservation.reservation_id)

    def test_explicit_session_is_strong_identity(self):
        first = resolve_source("movie_one", "movie", client_session="emby-playback-123",
            allow_reservation_reuse=True, reservation_ttl_seconds=3600)
        second = resolve_source("movie_one", "movie", client_session="emby-playback-123",
            allow_reservation_reuse=True, reservation_ttl_seconds=3600)
        self.assertEqual(first.reservation.reservation_id, second.reservation.reservation_id)
        self.assertEqual(second.reservation.identity_type, "explicit_session")
        self.assertNotIn("emby-playback", second.reservation.client_session)

    def test_duplicate_repair_keeps_one_matching_active_identity(self):
        first = self.resolve(fingerprint="repair-me")
        db = get_settings().data_dir / "media_router.db"
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id=?", (first.reservation.reservation_id,)).fetchone()
            conn.execute("""INSERT INTO broker_reservations
                (reservation_id,catalog_item_id,source_availability_id,provider_id,account_id,media_type,
                 location_ref,status,created_at,expires_at,client_fingerprint,identity_type,last_seen_at,last_action)
                VALUES ('duplicate',?,?,?,?,?,?,'active',?,?,?,?,?,'reservation_created')""",
                (row[2], row[3], row[4], row[5], row[6], row[7], row[9], row[10], row[14], "derived_fingerprint", row[9]))
        repaired = repair_duplicate_reservations()
        self.assertEqual(repaired.released_reservations, 1)
        self.assertEqual(len([row for row in list_reservations() if row.status == "active"]), 1)

    def test_distinct_clients_use_distinct_accounts_and_capacity_is_real(self):
        first = self.resolve(fingerprint="client-a")
        second = self.resolve(fingerprint="client-b")
        self.assertNotEqual(first.reservation.reservation_id, second.reservation.reservation_id)
        self.assertNotEqual(first.reservation.account_id, second.reservation.account_id)
        with self.assertRaises(BrokerUnavailable) as raised:
            self.resolve(fingerprint="client-c")
        self.assertEqual(raised.exception.detail.failure_code, "all_at_capacity")

    def test_identity_is_scoped_by_catalog_item_and_media_type(self):
        movie_one = self.resolve("movie_one", "movie", "same-client")
        movie_two = self.resolve("movie_two", "movie", "same-client")
        self.assertNotEqual(movie_one.reservation.reservation_id, movie_two.reservation.reservation_id)

    def test_live_and_movie_are_separate_playbacks(self):
        movie = self.resolve("movie_one", "movie", "same-client")
        live = self.resolve("live_one", "live", "same-client")
        self.assertNotEqual(movie.reservation.reservation_id, live.reservation.reservation_id)


if __name__ == "__main__":
    unittest.main()
