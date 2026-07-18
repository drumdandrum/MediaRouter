import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

from app.core.config import get_settings
from app.schemas.settings import SettingsUpdate
from app.schemas.providers import AccountCreate, ProviderCreate
from app.services.broker import BrokerUnavailable, ensure_broker_schema, list_reservations, release_reservation, repair_duplicate_reservations, resolve_source
from app.services.catalog import ensure_schema
from app.services.providers import create_account, create_provider
from app.services.runtime import runtime_client_address, runtime_client_fingerprint
from app.services.settings import update_app_settings
from app.services.logs import list_logs


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
        active = [row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].reuse_count, 5)

    def test_http_head_get_and_range_requests_share_runtime_reservation(self):
        client = TestClient(app, follow_redirects=False)
        head = client.head("/r/movie/movie_one", headers={"x-forwarded-for": "192.0.2.44:41000", "user-agent": "Emby/4.8.1"})
        first = client.get("/r/movie/movie_one", headers={"x-forwarded-for": "192.0.2.44:42000", "user-agent": "Emby/4.8.2", "range": "bytes=0-1023"})
        seek = client.get("/r/movie/movie_one", headers={"x-forwarded-for": "192.0.2.44:43000", "user-agent": "Emby/4.8.3", "range": "bytes=500000-"})
        self.assertEqual((head.status_code, first.status_code, seek.status_code), (302, 302, 302))
        self.assertEqual(first.headers["x-media-router-reservation-id"], seek.headers["x-media-router-reservation-id"])
        self.assertEqual(len([row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}]), 1)

    def test_http_generic_ffmpeg_to_unknown_playback_ua_coalesces_range_requests(self):
        client = TestClient(app, follow_redirects=False)
        first = client.get("/r/movie/movie_one", headers={"user-agent": "Lavf/60.3"})
        two_seconds_ago = (datetime.utcnow() - timedelta(seconds=2)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE broker_reservations SET created_at=?, last_seen_at=? WHERE reservation_id=?",
                         (two_seconds_ago, two_seconds_ago, first.headers["x-media-router-reservation-id"]))
        second = client.get("/r/movie/movie_one", headers={"user-agent": "MediaBrowser Playback Client",
            "range": "bytes=0-1023"})
        seek = client.get("/r/movie/movie_one", headers={"user-agent": "MediaBrowser Playback Client",
            "range": "bytes=500000-"})
        self.assertEqual((first.status_code, second.status_code, seek.status_code), (302, 302, 302))
        self.assertEqual(len({first.headers["x-media-router-reservation-id"],
                              second.headers["x-media-router-reservation-id"],
                              seek.headers["x-media-router-reservation-id"]}), 1)
        reservation = [row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}][0]
        self.assertEqual((reservation.alias_count, reservation.coalesced_reuse_count), (2, 1))

    def test_simultaneous_requests_are_atomic(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(pool.map(lambda _: self.resolve(fingerprint="simultaneous"), range(8)))
        self.assertEqual(len({decision.reservation.reservation_id for decision in decisions}), 1)
        self.assertEqual(len([row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}]), 1)

    def test_simultaneous_explicit_sessions_are_atomic(self):
        def acquire(_):
            return resolve_source("movie_one", "movie", client_session="playback-session-1",
                allow_reservation_reuse=True, reservation_ttl_seconds=3600)
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(pool.map(acquire, range(8)))
        self.assertEqual(len({decision.reservation.reservation_id for decision in decisions}), 1)
        active = [row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}]
        self.assertEqual((len(active), active[0].reuse_count), (1, 7))

    def test_slow_redirect_handoff_retries_reuse_committed_reservation(self):
        from app.api import runtime as runtime_api
        original = runtime_api.resolve_runtime

        def slow_handoff(*args, **kwargs):
            result = original(*args, **kwargs)
            time.sleep(0.08)
            return result

        def request(_):
            with TestClient(app, follow_redirects=False) as client:
                return client.get("/r/movie/movie_one", headers={"user-agent": "Emby/4.8.3"})

        with patch("app.api.runtime.resolve_runtime", side_effect=slow_handoff):
            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(request, range(6)))
        self.assertTrue(all(response.status_code == 302 for response in responses))
        self.assertEqual(len({response.headers["x-media-router-reservation-id"] for response in responses}), 1)
        active = [row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}]
        self.assertEqual((len(active), active[0].reuse_count), (1, 5))

    def test_proxy_headers_are_used_only_when_explicitly_trusted(self):
        headers = {"x-forwarded-for": "192.0.2.50:41000, 198.51.100.7", "cf-connecting-ip": "192.0.2.60"}
        self.assertEqual(runtime_client_address("10.0.0.9", headers), "10.0.0.9")
        update_app_settings(SettingsUpdate(trust_proxy_headers=True,
            trusted_proxy_client_header="x-forwarded-for", trusted_proxy_networks="10.0.0.0/8"))
        self.assertEqual(runtime_client_address("10.0.0.9", headers), "192.0.2.50")
        update_app_settings(SettingsUpdate(trusted_proxy_client_header="cf-connecting-ip"))
        self.assertEqual(runtime_client_address("10.0.0.9", headers), "192.0.2.60")

    def test_emby_probe_and_playback_fingerprints_coalesce_and_remain_aliases(self):
        first = resolve_source("movie_one", "movie", client_fingerprint="probe-fingerprint",
            origin_identity="same-origin", request_profile="generic_ffmpeg",
            allow_reservation_reuse=True, reservation_ttl_seconds=3600)
        two_seconds_ago = (datetime.utcnow() - timedelta(seconds=2)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE broker_reservations SET created_at=?, last_seen_at=? WHERE reservation_id=?",
                         (two_seconds_ago, two_seconds_ago, first.reservation.reservation_id))
        second = resolve_source("movie_one", "movie", client_fingerprint="playback-fingerprint",
            origin_identity="same-origin", request_profile="b23a6a8439c0",
            allow_reservation_reuse=True, reservation_ttl_seconds=3600)
        probe_again = resolve_source("movie_one", "movie", client_fingerprint="probe-fingerprint",
            origin_identity="same-origin", request_profile="generic_ffmpeg",
            allow_reservation_reuse=True, reservation_ttl_seconds=3600)
        playback_again = resolve_source("movie_one", "movie", client_fingerprint="playback-fingerprint",
            origin_identity="same-origin", request_profile="b23a6a8439c0",
            allow_reservation_reuse=True, reservation_ttl_seconds=3600)
        self.assertEqual(len({item.reservation.reservation_id for item in
            (first, second, probe_again, playback_again)}), 1)
        reservation = [row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}][0]
        self.assertEqual((reservation.alias_count, reservation.coalesced_reuse_count), (2, 1))
        self.assertTrue(reservation.startup_coalesced)
        self.assertTrue(any("coalescing_candidate_count=1" in row.message and "result=reused" in row.message
                            for row in list_logs()))

    def test_two_explicit_sessions_are_never_coalesced(self):
        first = resolve_source("movie_one", "movie", client_session="session-a",
            origin_identity="same-origin", request_profile="emby_server", allow_reservation_reuse=True)
        second = resolve_source("movie_one", "movie", client_session="session-b",
            origin_identity="same-origin", request_profile="emby_server", allow_reservation_reuse=True)
        self.assertNotEqual(first.reservation.reservation_id, second.reservation.reservation_id)
        self.assertEqual(len([row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}]), 2)

    def test_two_simultaneous_explicit_playbacks_are_not_merged(self):
        def acquire(session):
            return resolve_source("movie_one", "movie", client_session=session,
                origin_identity="same-origin", request_profile="emby_server",
                allow_reservation_reuse=True, reservation_ttl_seconds=3600)
        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = list(pool.map(acquire, ("real-session-a", "real-session-b")))
        self.assertEqual(len({decision.reservation.reservation_id for decision in decisions}), 2)
        self.assertEqual(len([row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}]), 2)

    def test_distinct_origins_are_not_coalesced(self):
        first = resolve_source("movie_one", "movie", client_fingerprint="probe-a",
            origin_identity="origin-a", request_profile="emby_ffmpeg_probe", allow_reservation_reuse=True)
        second = resolve_source("movie_one", "movie", client_fingerprint="playback-b",
            origin_identity="origin-b", request_profile="emby_playback_worker", allow_reservation_reuse=True)
        self.assertNotEqual(first.reservation.reservation_id, second.reservation.reservation_id)

    def test_ambiguous_startup_candidates_are_not_coalesced(self):
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE accounts SET max_simultaneous_streams=3")
        first = resolve_source("movie_one", "movie", stable_client_id="device-a",
            origin_identity="shared-origin", request_profile="emby_server", allow_reservation_reuse=True)
        second = resolve_source("movie_one", "movie", stable_client_id="device-b",
            origin_identity="shared-origin", request_profile="emby_server", allow_reservation_reuse=True)
        third = resolve_source("movie_one", "movie", client_fingerprint="probe-third",
            origin_identity="shared-origin", request_profile="emby_ffmpeg_probe", allow_reservation_reuse=True)
        self.assertEqual(len({first.reservation.reservation_id, second.reservation.reservation_id,
                              third.reservation.reservation_id}), 3)
        self.assertTrue(any("coalescing_candidate_count=2" in row.message and "result=ambiguous" in row.message
                            for row in list_logs()))

    def test_cloudflare_edge_changes_do_not_change_trusted_origin(self):
        update_app_settings(SettingsUpdate(trust_proxy_headers=True,
            trusted_proxy_client_header="cf-connecting-ip", trusted_proxy_networks="198.51.100.0/24"))
        headers = {"cf-connecting-ip": "192.0.2.88", "user-agent": "Emby Server/4.8.3"}
        first = runtime_client_address("198.51.100.10", headers)
        second = runtime_client_address("198.51.100.99", headers)
        self.assertEqual((first, second), ("192.0.2.88", "192.0.2.88"))
        spoofed = runtime_client_address("203.0.113.9", headers)
        self.assertEqual(spoofed, "203.0.113.9")

    def test_coalescing_window_expiry_keeps_playbacks_distinct(self):
        first = resolve_source("movie_one", "movie", client_fingerprint="old-probe",
            origin_identity="origin", request_profile="emby_ffmpeg_probe", allow_reservation_reuse=True)
        old = (datetime.utcnow() - timedelta(seconds=120)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE broker_reservations SET created_at=?, last_seen_at=? WHERE reservation_id=?",
                         (old, old, first.reservation.reservation_id))
        second = resolve_source("movie_one", "movie", client_fingerprint="later-playback",
            origin_identity="origin", request_profile="emby_playback_worker", allow_reservation_reuse=True,
            startup_coalescing_window_seconds=90)
        self.assertNotEqual(first.reservation.reservation_id, second.reservation.reservation_id)
        self.assertTrue(any("result=outside_window" in row.message for row in list_logs()))

    def test_stable_emby_device_id_outranks_changing_fingerprints(self):
        first = resolve_source("movie_one", "movie", client_fingerprint="probe",
            stable_client_id="device-123", origin_identity="origin", request_profile="emby_ffmpeg_probe",
            allow_reservation_reuse=True)
        second = resolve_source("movie_one", "movie", client_fingerprint="playback",
            stable_client_id="device-123", origin_identity="different-origin", request_profile="emby_playback_worker",
            allow_reservation_reuse=True)
        self.assertEqual(first.reservation.reservation_id, second.reservation.reservation_id)
        self.assertEqual(second.reservation.identity_type, "stable_client_id")

    def test_release_deactivates_aliases(self):
        first = resolve_source("movie_one", "movie", client_fingerprint="release-probe",
            origin_identity="origin", request_profile="emby_ffmpeg_probe", allow_reservation_reuse=True)
        resolve_source("movie_one", "movie", client_fingerprint="release-playback",
            origin_identity="origin", request_profile="emby_playback_worker", allow_reservation_reuse=True)
        release_reservation(first.reservation.reservation_id)
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            active_aliases = conn.execute("SELECT COUNT(*) FROM broker_reservation_identity_aliases WHERE active=1").fetchone()[0]
        self.assertEqual(active_aliases, 0)
        replacement = resolve_source("movie_one", "movie", client_fingerprint="release-playback",
            origin_identity="origin", request_profile="emby_playback_worker", allow_reservation_reuse=True)
        self.assertNotEqual(first.reservation.reservation_id, replacement.reservation.reservation_id)

    def test_fingerprint_diagnostics_are_categorical_and_privacy_safe(self):
        with TestClient(app, follow_redirects=False) as client:
            response = client.get("/r/movie/movie_one", headers={"user-agent": "Emby Server/4.8.3",
                "range": "bytes=123-456", "authorization": "Bearer do-not-log", "cookie": "secret-cookie"})
        self.assertEqual(response.status_code, 302)
        diagnostic = next(row.message for row in list_logs() if "fingerprint_inputs" in row.message)
        self.assertIn("ua_family=emby_server", diagnostic)
        self.assertIn("ua_signature=", diagnostic)
        self.assertIn("range_present=True", diagnostic)
        self.assertIn("input_signature=", diagnostic)
        self.assertNotIn("do-not-log", diagnostic)
        self.assertNotIn("secret-cookie", diagnostic)

    def test_active_playback_identity_has_unique_database_key(self):
        decision = self.resolve(fingerprint="unique-key")
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            row = conn.execute("SELECT playback_identity_key FROM broker_reservations WHERE reservation_id=?",
                (decision.reservation.reservation_id,)).fetchone()
            indexes = {item[1] for item in conn.execute("PRAGMA index_list(broker_reservations)").fetchall()}
        self.assertTrue(row[0])
        self.assertIn("uq_broker_consuming_playback_identity", indexes)

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
        self.assertEqual(len([row for row in list_reservations() if row.lifecycle_state in {"provisional", "active"}]), 1)

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
