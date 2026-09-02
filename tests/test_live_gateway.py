import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.migrations import migrate_database
from app.main import app
from app.schemas.providers import AccountCreate, ProviderCreate
from app.services.broker import list_reservations, resolve_source
from app.services.live_gateway import LiveGatewaySession, open_live_gateway
from app.services.providers import create_account, create_provider


class _UpstreamHandler(BaseHTTPRequestHandler):
    requests = []
    hold_release = threading.Event()

    def log_message(self, *_args):
        pass

    def do_GET(self):
        type(self).requests.append((self.path, self.headers.get("Range")))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/stream")
            self.end_headers()
            return
        if self.path == "/fail":
            self.send_response(503)
            self.end_headers()
            return
        if self.path == "/range":
            self.send_response(206)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Range", "bytes 2-5/6")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"2345")
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        if self.path == "/hold":
            self.end_headers()
            type(self).hold_release.wait(5)
            self.wfile.write(b"held")
            return
        self.send_header("Content-Length", "6")
        self.end_headers()
        self.wfile.write(b"stream")


class LiveGatewayIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        migrate_database()
        provider = create_provider(ProviderCreate(friendly_name="Provider"))
        self.accounts = [
            create_account(AccountCreate(provider_id=provider.id, friendly_name="Stream 1", max_simultaneous_streams=1, weight=100)),
            create_account(AccountCreate(provider_id=provider.id, friendly_name="Stream 2", max_simultaneous_streams=1, weight=90)),
        ]
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            for item in ("live_stream", "live_range", "live_redirect", "live_fail", "live_hold"):
                conn.execute("INSERT INTO catalog_items(internal_id,media_type,title,normalized_title,confidence,created_at,updated_at) VALUES (?,?,?,?,'high',?,?)", (item, "channel", item, item, now, now))
            sources = [
                ("live_stream", 0, "/stream"), ("live_stream", 1, "/stream"),
                ("live_range", 0, "/range"),
                ("live_redirect", 0, "/redirect"),
                ("live_fail", 0, "/fail"), ("live_fail", 1, "/stream"),
                ("live_hold", 0, "/hold"), ("live_hold", 1, "/hold"),
            ]
            for item, account_index, path in sources:
                conn.execute("INSERT INTO source_availability(catalog_internal_id,provider_id,account_id,location_ref,media_type,enabled,last_seen_at,created_at,updated_at) VALUES (?,?,?,?, 'channel',1,?,?,?)",
                             (item, provider.id, self.accounts[account_index].id, self.base + path, now, now, now))
        _UpstreamHandler.requests = []
        _UpstreamHandler.hold_release.clear()
        self.client = TestClient(app)

    def tearDown(self):
        _UpstreamHandler.hold_release.set()
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def _active_count(self):
        return len([r for r in list_reservations() if r.lifecycle_state in {"provisional", "active"}])

    def test_get_commits_before_upstream_and_contains_no_provider_url(self):
        from app.services import live_gateway
        original = live_gateway._open_upstream

        def assert_committed(url, headers):
            self.assertEqual(self._active_count(), 1)
            return original(url, headers)

        with patch("app.services.live_gateway._open_upstream", side_effect=assert_committed):
            response = self.client.get("/r/live/live_stream")
        self.assertEqual((response.status_code, response.content), (200, b"stream"))
        self.assertNotIn("location", response.headers)
        self.assertNotIn(self.base, response.text)
        self.assertEqual(self._active_count(), 0)

    def test_public_apis_do_not_expose_provider_stream_url(self):
        catalog = self.client.get("/api/catalog/live_stream/sources").text
        decision = self.client.post("/api/broker/resolve", json={
            "catalog_item_id": "live_stream", "media_type": "channel", "client_session": "api-session",
        }).text
        for body in (catalog, decision):
            self.assertNotIn(self.base, body)
            self.assertNotIn("/stream", body)
        self.assertIn("redacted-provider-url", catalog)
        self.assertIn("/r/live/live_stream?ticket=", decision)

    def test_head_rejects_without_reservation_or_upstream(self):
        response = self.client.head("/r/live/live_stream")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["allow"], "GET")
        self.assertNotIn("location", response.headers)
        self.assertEqual((self._active_count(), _UpstreamHandler.requests), (0, []))

    def test_range_and_response_metadata_are_proxied(self):
        response = self.client.get("/r/live/live_range", headers={"Range": "bytes=2-5"})
        self.assertEqual((response.status_code, response.content), (206, b"2345"))
        self.assertEqual(response.headers["content-range"], "bytes 2-5/6")
        self.assertIn(("/range", "bytes=2-5"), _UpstreamHandler.requests)

    def test_redirect_is_internal(self):
        response = self.client.get("/r/live/live_redirect")
        self.assertEqual((response.status_code, response.content), (200, b"stream"))
        self.assertNotIn("location", response.headers)
        self.assertEqual([x[0] for x in _UpstreamHandler.requests], ["/redirect", "/stream"])

    def test_failed_source_releases_then_alternate_succeeds(self):
        response = self.client.get("/r/live/live_fail")
        self.assertEqual((response.status_code, response.content), (200, b"stream"))
        reservations = list_reservations()
        self.assertEqual(len(reservations), 2)
        self.assertEqual({r.release_reason for r in reservations}, {"upstream_terminal_status", "live_gateway_eof"})
        self.assertEqual(self._active_count(), 0)

    def test_all_at_capacity_attempts_no_upstream(self):
        resolve_source("live_stream", "channel", client_session="one", lifecycle_enabled=True)
        resolve_source("live_stream", "channel", client_session="two", lifecycle_enabled=True)
        before = len(_UpstreamHandler.requests)
        response = self.client.get("/r/live/live_stream")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(_UpstreamHandler.requests), before)

    def test_same_nat_and_user_agent_get_independent_capacity(self):
        from app.services.runtime import resolve_runtime
        sessions = []
        for ownership in ("connection-one", "connection-two"):
            def acquire(excluded, ownership=ownership):
                payload, target = resolve_runtime(
                    "live", "live_hold", client_fingerprint=ownership,
                    reserve=True, excluded_source_availability_ids=excluded,
                )
                return payload.broker_decision.reservation, target
            sessions.append(open_live_gateway(acquire, {"user-agent": "same-player"}))
        self.assertEqual(self._active_count(), 2)
        _UpstreamHandler.hold_release.set()
        for session in sessions:
            self.assertEqual(b"".join(session.iter_bytes()), b"held")
        self.assertEqual(self._active_count(), 0)

    def test_generic_gateway_connections_do_not_startup_coalesce(self):
        from app.services.runtime import RuntimeResolveUnavailable, resolve_runtime
        first, _ = resolve_runtime(
            "live", "live_range", client_fingerprint="connection-one",
            origin_identity="same-nat", request_profile="same-player",
            allow_startup_coalescing=False,
        )
        with self.assertRaises(RuntimeResolveUnavailable):
            resolve_runtime(
                "live", "live_range", client_fingerprint="connection-two",
                origin_identity="same-nat", request_profile="same-player",
                allow_startup_coalescing=False,
            )
        self.assertEqual(self._active_count(), 1)
        from app.services.broker import release_reservation
        release_reservation(first.reservation_id, reason="test_cleanup")

    def test_mapping_is_not_required_before_upstream(self):
        for mapped in (False, True):
            with self.subTest(mapped=mapped):
                if mapped:
                    now = datetime.utcnow().isoformat()
                    with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
                        conn.execute("INSERT INTO emby_channel_mappings(emby_server_id,integration_id,emby_item_id,emby_channel_name,catalog_item_id,mapping_source,created_at,updated_at) VALUES ('server','server','item','name','live_stream','manual',?,?)", (now, now))
                from app.services import live_gateway
                original = live_gateway._open_upstream
                with patch("app.services.live_gateway._open_upstream", side_effect=lambda url, headers: (self.assertEqual(self._active_count(), 1), original(url, headers))[1]):
                    self.assertEqual(self.client.get("/r/live/live_stream").status_code, 200)


class _FakeReservation:
    reservation_id = "reservation"
    source_availability_id = 1


class _FakeUpstream:
    status = 200
    headers = Message()
    def __init__(self, chunks=None):
        self.chunks = list(chunks or [b"data", b""])
        self.closed = 0
    def read(self, _size):
        value = self.chunks.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value
    def close(self):
        self.closed += 1


class LiveGatewayLifecycleTests(unittest.TestCase):
    def test_heartbeat_is_bounded_and_eof_releases_once(self):
        upstream = _FakeUpstream([b"one", b"two", b""])
        session = LiveGatewaySession("reservation", 1, upstream, 200, {}, heartbeat_interval_seconds=1)
        with patch("app.services.live_gateway.time.monotonic", side_effect=[0, 2, 2.5]), \
             patch("app.services.live_gateway.heartbeat_reservation") as heartbeat, \
             patch("app.services.live_gateway.release_gateway_reservation_if_unbound") as release:
            self.assertEqual(b"".join(session.iter_bytes()), b"onetwo")
            session.release("duplicate")
        heartbeat.assert_called_once_with("reservation", source="live_gateway_activity")
        release.assert_called_once_with("reservation", reason="live_gateway_eof")
        self.assertEqual(upstream.closed, 1)

    def test_overlapping_reconnect_releases_only_after_last_owner(self):
        first_upstream = _FakeUpstream([b"first"])
        second_upstream = _FakeUpstream([b"second"])
        first = LiveGatewaySession("shared-reservation", 1, first_upstream, 200, {})
        second = LiveGatewaySession("shared-reservation", 1, second_upstream, 200, {})
        with patch("app.services.live_gateway.release_gateway_reservation_if_unbound") as release:
            first.release("first_connection_closed")
            release.assert_not_called()
            second.release("last_connection_closed")
        release.assert_called_once_with("shared-reservation", reason="last_connection_closed")
        self.assertEqual(first_upstream.closed, 1)
        self.assertEqual(second_upstream.closed, 1)

    def test_disconnect_cancellation_timeout_and_failure_release_once(self):
        for label, chunks in (
            ("disconnect", [b"one", b"two"]),
            ("timeout", [TimeoutError("timeout")]),
            ("failure", [OSError("failure")]),
        ):
            with self.subTest(label=label):
                upstream = _FakeUpstream(chunks)
                session = LiveGatewaySession("reservation", 1, upstream, 200, {})
                with patch("app.services.live_gateway.release_gateway_reservation_if_unbound") as release:
                    iterator = session.iter_bytes()
                    if label == "disconnect":
                        self.assertEqual(next(iterator), b"one")
                        iterator.close()
                        expected = "live_gateway_disconnect"
                    else:
                        with self.assertRaises((TimeoutError, OSError)):
                            next(iterator)
                        expected = "live_gateway_stream_failure"
                    session.release("duplicate")
                release.assert_called_once_with("reservation", reason=expected)

    def test_bounded_failover_stops_and_scrubs_error(self):
        acquired = []
        def acquire(excluded):
            acquired.append(set(excluded))
            reservation = type("Reservation", (), {"reservation_id": str(len(acquired)), "source_availability_id": len(acquired)})()
            return reservation, "https://user:password@provider.invalid/live/secret/secret/channel"
        with patch("app.services.live_gateway._open_upstream", side_effect=__import__("app.services.live_gateway", fromlist=["LiveGatewayError"]).LiveGatewayError("upstream_connection_failed", "The live provider connection failed.")), \
             patch("app.services.live_gateway.release_reservation") as release:
            with self.assertRaisesRegex(Exception, "live provider connection failed") as raised:
                open_live_gateway(acquire, {}, max_attempts=2)
        self.assertNotIn("provider.invalid", str(raised.exception))
        self.assertEqual(len(acquired), 2)
        self.assertEqual(release.call_count, 2)


class LiveGatewayAsyncLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_cancellation_releases_once(self):
        upstream = _FakeUpstream([b"one", b"two"])
        session = LiveGatewaySession("async-reservation", 1, upstream, 200, {})
        with patch("app.services.live_gateway.release_gateway_reservation_if_unbound") as release:
            iterator = session.iter_bytes_async()
            self.assertEqual(await anext(iterator), b"one")
            await iterator.aclose()
            session.release("duplicate")
        release.assert_called_once_with("async-reservation", reason="live_gateway_disconnect")


if __name__ == "__main__":
    unittest.main()
