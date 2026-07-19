import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.schemas.integrations import EmbySettingsUpdate
from app.schemas.providers import AccountCreate, ProviderCreate
from app.services.broker import ensure_broker_schema, get_status, list_reservations, resolve_source
from app.services.catalog import ensure_schema
from app.services.providers import create_account, create_provider
from pydantic import ValidationError


class EmbyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        ensure_schema()
        ensure_broker_schema()
        from app.services.emby import ensure_emby_schema
        ensure_emby_schema()
        provider = create_provider(ProviderCreate(friendly_name="Provider"))
        self.account = create_account(AccountCreate(provider_id=provider.id, friendly_name="Stream", max_simultaneous_streams=2))
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            for item, media in (("movie_one", "movie"), ("movie_two", "movie"), ("live_one", "channel"), ("live_two", "channel"), ("episode_one", "episode")):
                conn.execute("INSERT INTO catalog_items(internal_id,media_type,title,normalized_title,confidence,created_at,updated_at) VALUES (?,?,?,?, 'high',?,?)",
                             (item, media, item, item, now, now))
                conn.execute("""INSERT INTO source_availability
                    (catalog_internal_id,provider_id,account_id,location_ref,media_type,enabled,last_seen_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,1,?,?,?)""", (item, provider.id, self.account.id,
                    f"https://provider.invalid/{media}/secret/secret/stream", media, now, now, now))

    def tearDown(self):
        get_settings.cache_clear()
        os.environ.pop("MEDIA_ROUTER_DATA_DIR", None)
        self.temp.cleanup()

    def _reservation(self, item="movie_one", media="movie", session="session-1"):
        return resolve_source(item, media, client_session=session, allow_reservation_reuse=True,
            lifecycle_enabled=True).reservation

    def _payload(self, item="movie_one", route="movie", session="session-1", play="play-1", paused=False):
        return [{"Id": session, "DeviceId": "device-1", "DeviceName": "Living Room",
            "Client": "Emby Theater", "UserId": "user-1", "UserName": "Viewer",
            "NowPlayingItem": {"Id": f"emby-{item}", "Name": item, "Type": "Movie",
                "Path": f"http://router:8088/r/{route}/{item}"},
            "PlayState": {"PlaySessionId": play, "MediaSourceId": "source-1",
                "PositionTicks": 1000, "IsPaused": paused}}]

    def _payload_without_runtime_url(self, *, session="session-1", play="play-1", device="device-1",
                                     name="Unrelated Emby title"):
        return [{"Id": session, "DeviceId": device, "DeviceName": "Living Room",
            "Client": "Emby Theater", "UserId": "user-1", "UserName": "Viewer",
            "NowPlayingItem": {"Id": "emby-native-item", "Name": name, "Type": "Movie",
                "Path": "/var/lib/emby/transcoding-temp/native-item.mkv"},
            "PlayState": {"PlaySessionId": play, "MediaSourceId": "emby-media-source",
                "PositionTicks": 1000, "IsPaused": False}}]

    def _live_payload(self, session="live-session-1", play="live-play-1"):
        return [{"Id": session, "DeviceId": "live-device", "DeviceName": "Living Room",
            "Client": "Emby Theater", "UserId": "user-1", "UserName": "Viewer",
            "NowPlayingItem": {"Id": "emby-live-one", "ChannelId": "live_one",
                "Name": "live_one", "Type": "TvChannel",
                "Path": "http://router:8088/r/live/live_one"},
            "PlayState": {"PlaySessionId": play, "MediaSourceId": "source-live",
                "PositionTicks": 1000, "IsPaused": False}}]

    def _mapped_live_payload(self, item_id, catalog_id, session, media_source):
        payload = self._live_payload(session=session, play=f"play-{session}")
        payload[0]["NowPlayingItem"] = {"Id": item_id, "Name": catalog_id, "Type": "TvChannel"}
        payload[0]["PlayState"]["MediaSourceId"] = media_source
        return payload

    def _channel_mapping(self, item_id, catalog_id, media_source):
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("""INSERT INTO emby_channel_mappings
                (emby_server_id,emby_item_id,emby_media_source_id,emby_channel_name,catalog_item_id,mapping_source,created_at,updated_at)
                VALUES ('server',?,?,?,?, 'manual',?,?)""",
                (item_id, media_source, catalog_id, catalog_id, now, now))

    def _observation(self, reservation, *, profile="emby_server", stable="device-1",
                     address="weak-address"):
        from app.services.emby import record_runtime_correlation_observation
        record_runtime_correlation_observation(
            reservation_id=reservation.reservation_id,
            catalog_item_id=reservation.catalog_item_id,
            media_type=reservation.media_type,
            route_type=reservation.media_type,
            request_identity_type="stable_client_id",
            request_identity=stable,
            stable_emby_identifier=stable,
            request_profile=profile,
            address_signature=address,
            user_agent_signature="safe-agent-signature",
        )

    def test_settings_defaults_validation_redaction_and_secret_preservation(self):
        from app.services.emby import _private_settings, get_emby_settings, update_emby_settings
        defaults = get_emby_settings()
        self.assertFalse(defaults.enabled)
        self.assertFalse(defaults.emby_runtime_correlation_enabled)
        self.assertEqual((defaults.poll_interval_seconds, defaults.release_grace_seconds,
                          defaults.request_timeout_seconds, defaults.verify_tls), (10, 30, 10, True))
        updated = update_emby_settings(EmbySettingsUpdate(server_url="http://emby.local/", api_key="top-secret"))
        self.assertEqual(updated.server_url, "http://emby.local")
        self.assertTrue(updated.has_api_key)
        self.assertNotIn("api_key", updated.model_dump())
        update_emby_settings(EmbySettingsUpdate(api_key=""))
        self.assertEqual(_private_settings()["api_key"], "top-secret")
        self.assertNotIn("top-secret", json.dumps(get_emby_settings().model_dump()))
        with self.assertRaises(ValidationError):
            EmbySettingsUpdate(poll_interval_seconds=1)
        with self.assertRaises(ValidationError):
            EmbySettingsUpdate(server_url="emby-without-scheme")
        for unsafe_url in ("http://user:password@emby.local", "http://emby.local?api_key=secret", "http://emby.local/#token"):
            with self.assertRaises(ValidationError):
                EmbySettingsUpdate(server_url=unsafe_url)

    def test_disabled_polling_makes_no_network_request(self):
        from app.services.emby import poll_emby_once
        with patch("app.services.emby._request_json") as request:
            status = poll_emby_once()
        request.assert_not_called()
        self.assertEqual(status.health_state, "disabled")

    def test_connection_success_auth_timeout_and_malformed_are_sanitized(self):
        from app.services.emby import EmbyError, test_emby_connection
        with patch("app.services.emby._request_json", return_value={"Id": "server", "ServerName": "Home", "Version": "4.8"}):
            result = test_emby_connection()
            self.assertTrue(result.success)
            self.assertEqual((result.server_name, result.server_version), ("Home", "4.8"))
        for state, message in (("authentication_failed", "Emby rejected the configured credentials."),
                               ("degraded", "Emby could not be reached before the request timeout."),
                               ("error", "Emby returned an unexpected response.")):
            with patch("app.services.emby._request_json", side_effect=EmbyError(message, state)):
                result = test_emby_connection()
                self.assertFalse(result.success)
                self.assertEqual(result.health_state, state)
                self.assertNotIn("secret", result.message.lower())

    def test_transient_connection_refusal_retries_with_configured_timeout(self):
        from app.services.emby import _request_json

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"Id":"server"}'

        settings = {"server_url": "http://emby.local", "api_key": "secret",
                    "request_timeout_seconds": 2, "verify_tls": True}
        with patch("app.services.emby.urlopen", side_effect=[URLError(ConnectionRefusedError()), Response()]) as request, \
             patch("app.services.emby.time.sleep"):
            self.assertEqual(_request_json("/System/Info", settings)["Id"], "server")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(0 < call.kwargs["timeout"] <= 2 for call in request.call_args_list))

    def test_runtime_url_normalization_for_all_media_types(self):
        from app.services.emby import normalize_emby_sessions
        cases = (("live_one", "live", "channel"), ("movie_one", "movie", "movie"),
                 ("episode_one", "episode", "episode"))
        for item, route, media in cases:
            session = normalize_emby_sessions(self._payload(item, route), "server")[0]
            self.assertEqual((session.catalog_item_id, session.media_type), (item, media))

    def test_authoritative_live_playback_creates_active_reservation_without_runtime_observation(self):
        from app.services.emby import list_emby_bindings, normalize_emby_sessions, reconcile_emby_sessions
        sessions = normalize_emby_sessions(self._live_payload(), "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        reservations = list_reservations()
        self.assertEqual((matched, unmatched, len(reservations)), (1, 0, 1))
        self.assertEqual((reservations[0].catalog_item_id, reservations[0].media_type,
                          reservations[0].identity_type, reservations[0].lifecycle_state),
                         ("live_one", "channel", "explicit_session", "active"))
        self.assertEqual(get_status().consuming_reservations, 1)
        self.assertEqual(list_emby_bindings()[0].reservation_id, reservations[0].reservation_id)

    def test_durable_provider_id_resolves_without_runtime_path(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        payload = self._live_payload()
        payload[0]["NowPlayingItem"].pop("Path")
        payload[0]["NowPlayingItem"].pop("ChannelId")
        payload[0]["NowPlayingItem"]["Id"] = "opaque-emby-item"
        payload[0]["NowPlayingItem"]["ProviderIds"] = {"MediaRouter": "live_one"}
        sessions = normalize_emby_sessions(payload, "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        self.assertEqual((matched, unmatched, sessions[0].catalog_item_id), (1, 0, "live_one"))
        self.assertEqual(list_reservations()[0].identity_type, "explicit_session")

    def test_vlc_runtime_context_does_not_block_durable_emby_allocation(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        unrelated = self._reservation("movie_one", "movie", "vlc-runtime")
        self._observation(unrelated, profile="generic_http_client", stable=None, address="vlc-address")
        sessions = normalize_emby_sessions(self._live_payload(session="emby-live-vlc"), "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        live = [row for row in list_reservations() if row.catalog_item_id == "live_one"]
        self.assertEqual((matched, unmatched, len(live)), (1, 0, 1))
        self.assertEqual((live[0].identity_type, live[0].lifecycle_state), ("explicit_session", "active"))
        self.assertEqual(sessions[0].rejected_for_client_context_count, 0)

    def test_sparse_emby_session_is_enriched_from_full_item_before_allocation(self):
        from app.services.emby import poll_emby_once, update_emby_settings
        payload = self._live_payload(session="sparse-live")
        payload[0]["NowPlayingItem"] = {"Id": "emby-channel-id", "Name": "live_one", "Type": "TvChannel"}
        update_emby_settings(EmbySettingsUpdate(enabled=True, server_url="http://emby", api_key="key"))
        detail = {"Id": "emby-channel-id", "Name": "live_one", "Type": "TvChannel",
            "Path": "http://router:8088/r/live/live_one?mr_catalog_id=live_one",
            "ProviderIds": {"MediaRouter": "live_one"}, "MediaSources": []}
        with patch("app.services.emby._request_json", side_effect=[
            {"Id": "server", "ServerName": "Home"}, payload, detail]) as request:
            status = poll_emby_once()
        self.assertEqual(request.call_count, 3)
        self.assertEqual((status.matched_playback_count, status.unmatched_playback_count), (1, 0))
        reservation = list_reservations()[0]
        self.assertEqual((reservation.catalog_item_id, reservation.identity_type, reservation.lifecycle_state),
                         ("live_one", "explicit_session", "active"))

    def test_persisted_item_and_media_source_mappings_resolve_sparse_sessions(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("""INSERT INTO emby_channel_mappings
                (emby_server_id,emby_item_id,emby_media_source_id,emby_channel_name,catalog_item_id,mapping_source,created_at,updated_at)
                VALUES ('server','15747','source-opaque','24/7 EDDY MURPHY','live_one','manual',?,?)""", (now, now))
        for item_id, media_source, session_id in (("15747", "different", "mapped-item"),
                                                   ("different-item", "source-opaque", "mapped-source")):
            payload = self._live_payload(session=session_id)
            payload[0]["NowPlayingItem"] = {"Id": item_id, "Name": "24/7 EDDY MURPHY", "Type": "TvChannel"}
            payload[0]["PlayState"]["MediaSourceId"] = media_source
            sessions = normalize_emby_sessions(payload, "server")
            matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
            self.assertEqual((matched, unmatched, sessions[0].catalog_item_id), (1, 0, "live_one"))
            self.assertTrue(sessions[0].correlation_method.startswith("emby_channel_mapping_"))

    def test_channel_refresh_builds_mapping_from_mr_provider_marker(self):
        from app.services.emby import list_emby_channel_mappings, refresh_emby_channel_mappings
        channels = {"Items": [{"Id": "15747", "Name": "24/7 EDDY MURPHY", "Type": "TvChannel",
            "ProviderIds": {"TvgId": "mr:live_one"}, "MediaSources": [{"Id": "source-opaque"}], "Tags": []}]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            result = refresh_emby_channel_mappings()
        mapping = list_emby_channel_mappings()[0]
        self.assertEqual((result.discovered, result.mapped, result.unmapped), (1, 1, 0))
        self.assertEqual((mapping.emby_item_id, mapping.emby_media_source_id, mapping.catalog_item_id,
                          mapping.mapping_source), ("15747", "source-opaque", "live_one", "automatic_marker"))

    def test_mapping_preview_preserves_manual_and_guards_title_fallback(self):
        from app.services.emby import link_emby_channel, preview_emby_channel_mappings, refresh_emby_channel_mappings, list_emby_channel_mappings
        link_emby_channel("server", "manual-item", "live_one")
        channels = {"Items": [
            {"Id": "manual-item", "Name": "changed", "Type": "TvChannel", "ProviderIds": {"TvgId": "mr:live_two"}},
            {"Id": "unique-title", "Name": "live_two", "Type": "TvChannel"},
            {"Id": "duplicate-a", "Name": "duplicate", "Type": "TvChannel"},
            {"Id": "duplicate-b", "Name": "duplicate", "Type": "TvChannel"},
        ]}
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            now = datetime.utcnow().isoformat()
            conn.execute("INSERT INTO catalog_items(internal_id,media_type,title,normalized_title,confidence,created_at,updated_at) VALUES ('live_duplicate','channel','duplicate','duplicate','high',?,?)", (now, now))
            conn.execute("INSERT INTO source_availability(catalog_internal_id,provider_id,account_id,location_ref,media_type,enabled,last_seen_at,created_at,updated_at) VALUES ('live_duplicate',?,?,?,'channel',1,?,?,?)",
                         (self.account.provider_id, self.account.id, "https://provider.invalid/channel/duplicate", now, now, now))
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            preview = preview_emby_channel_mappings()
        by_id = {item.emby_item_id: item for item in preview.items}
        self.assertEqual((by_id["manual-item"].status, by_id["manual-item"].catalog_item_id), ("conflict", "live_one"))
        self.assertEqual((by_id["unique-title"].status, by_id["unique-title"].match_source,
                          by_id["unique-title"].catalog_item_id), ("automatic", "automatic_title", "live_two"))
        self.assertTrue(all(by_id[item].status == "ambiguous" for item in ("duplicate-a", "duplicate-b")))
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            refresh_emby_channel_mappings()
        mappings = {item.emby_item_id: item for item in list_emby_channel_mappings()}
        self.assertEqual((mappings["manual-item"].catalog_item_id, mappings["manual-item"].mapping_source), ("live_one", "manual"))
        self.assertEqual(mappings["unique-title"].mapping_source, "automatic_title")
        self.assertIsNone(mappings["duplicate-a"].catalog_item_id)

    def test_mapping_page_bounds_large_lineups(self):
        from app.services.emby import page_emby_channel_mappings
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.executemany("""INSERT INTO emby_channel_mappings
                (emby_server_id,integration_id,emby_item_id,emby_channel_name,mapping_source,created_at,updated_at)
                VALUES ('server','server',?,?,'unmapped',?,?)""",
                ((str(index), f"Channel {index}", now, now) for index in range(1005)))
        page = page_emby_channel_mappings()
        self.assertEqual((page.total, page.limit, page.offset, len(page.items)), (1005, 100, 0, 100))

    def test_manual_item_mapping_can_be_created_before_channel_refresh_and_drives_lifecycle(self):
        from app.main import app
        from app.services.emby import list_emby_bindings, normalize_emby_sessions, reconcile_emby_sessions
        from app.services.logs import list_logs
        client = TestClient(app)
        linked = client.put("/api/integrations/emby/channel-mappings/server/237858",
                            json={"catalog_item_id": "live_two", "emby_media_source_id": "hgtv-source"})
        self.assertEqual(linked.status_code, 200)
        self.assertEqual((linked.json()["emby_item_id"], linked.json()["catalog_item_id"],
                          linked.json()["mapping_source"]), ("237858", "live_two", "manual"))
        self.assertEqual(linked.json()["integration_id"], "server")
        self.assertEqual(linked.json()["emby_media_source_id"], "hgtv-source")
        listed = client.get("/api/integrations/emby/channel-mappings")
        self.assertEqual((listed.status_code, len(listed.json())), (200, 1))
        payload = self._mapped_live_payload("237858", "live_two", "hgtv-endpoint", "hgtv-source")
        reconcile_emby_sessions(normalize_emby_sessions(payload, "server"), server_id="server", release_grace_seconds=30)
        first = list_reservations()[0]
        reconcile_emby_sessions(normalize_emby_sessions(payload, "server"), server_id="server", release_grace_seconds=30)
        current = list_reservations()[0]
        self.assertEqual((len(list_reservations()), first.reservation_id, current.reservation_id),
                         (1, current.reservation_id, current.reservation_id))
        self.assertEqual((current.identity_type, current.lifecycle_state, get_status().consuming_reservations),
                         ("explicit_session", "active", 1))
        self.assertEqual(list_emby_bindings()[0].reservation_id, current.reservation_id)
        messages = [row.message for row in list_logs()]
        for event in ("emby_catalog_identity_resolved", "emby_reservation_created",
                      "emby_binding_created", "emby_binding_refreshed"):
            self.assertTrue(any(event in message for message in messages), event)
        self.assertEqual(client.delete("/api/integrations/emby/channel-mappings/server/237858").status_code, 204)
        self.assertEqual(client.get("/api/integrations/emby/channel-mappings").json(), [])

    def test_two_session_lifecycles_are_independent_and_refresh_without_duplicates(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        self._channel_mapping("15778", "live_one", "source-die-hard")
        self._channel_mapping("237858", "live_two", "source-hgtv")
        raw = self._mapped_live_payload("15778", "live_one", "endpoint-a", "source-die-hard") + \
              self._mapped_live_payload("237858", "live_two", "endpoint-b", "source-hgtv")
        reconcile_emby_sessions(normalize_emby_sessions(raw, "server"), server_id="server", release_grace_seconds=5)
        first_ids = {row.reservation_id for row in list_reservations() if row.lifecycle_state == "active"}
        self.assertEqual((len(first_ids), get_status().consuming_reservations), (2, 2))
        reconcile_emby_sessions(normalize_emby_sessions(raw, "server"), server_id="server", release_grace_seconds=5)
        active = [row for row in list_reservations() if row.lifecycle_state == "active"]
        self.assertEqual({row.reservation_id for row in active}, first_ids)
        self.assertTrue(all(row.last_confirmation_source == "emby_playback_heartbeat" for row in active))

    def test_same_session_channel_switch_transitions_only_its_reservation(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        self._channel_mapping("15778", "live_one", "source-die-hard")
        self._channel_mapping("237858", "live_two", "source-hgtv")
        initial = self._mapped_live_payload("15778", "live_one", "endpoint-a", "source-die-hard") + \
                  self._mapped_live_payload("237858", "live_two", "endpoint-b", "source-hgtv")
        reconcile_emby_sessions(normalize_emby_sessions(initial, "server"), server_id="server", release_grace_seconds=5)
        before = {row.catalog_item_id: row.reservation_id for row in list_reservations() if row.lifecycle_state == "active"}
        switched = self._mapped_live_payload("237858", "live_two", "endpoint-a", "source-hgtv") + initial[1:]
        reconcile_emby_sessions(normalize_emby_sessions(switched, "server"), server_id="server", release_grace_seconds=5)
        rows = {row.reservation_id: row for row in list_reservations()}
        self.assertNotEqual(rows[before["live_one"]].lifecycle_state, "active")
        endpoint_b = [binding for binding in __import__("app.services.emby", fromlist=["list_emby_bindings"]).list_emby_bindings()
                      if binding.emby_session_id == "endpoint-b" and binding.released_at is None][0]
        self.assertEqual(endpoint_b.reservation_id, before["live_two"])

    def test_disappearing_one_endpoint_releases_only_its_reservation(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        self._channel_mapping("15778", "live_one", "source-die-hard")
        self._channel_mapping("237858", "live_two", "source-hgtv")
        first = self._mapped_live_payload("15778", "live_one", "endpoint-a", "source-die-hard")
        second = self._mapped_live_payload("237858", "live_two", "endpoint-b", "source-hgtv")
        reconcile_emby_sessions(normalize_emby_sessions(first + second, "server"), server_id="server", release_grace_seconds=5)
        ids = {row.catalog_item_id: row.reservation_id for row in list_reservations() if row.lifecycle_state == "active"}
        reconcile_emby_sessions(normalize_emby_sessions(second, "server"), server_id="server", release_grace_seconds=5)
        old = (datetime.utcnow() - timedelta(seconds=6)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE emby_playback_bindings SET missing_since=? WHERE emby_session_id='endpoint-a' AND released_at IS NULL", (old,))
        reconcile_emby_sessions(normalize_emby_sessions(second, "server"), server_id="server", release_grace_seconds=5)
        rows = {row.reservation_id: row for row in list_reservations()}
        self.assertEqual(rows[ids["live_one"]].lifecycle_state, "released")
        self.assertEqual(rows[ids["live_two"]].lifecycle_state, "active")

    def test_repeated_live_polls_refresh_same_reservation_without_duplicate(self):
        from app.services.emby import list_emby_bindings, normalize_emby_sessions, reconcile_emby_sessions
        payload = normalize_emby_sessions(self._live_payload(), "server")
        reconcile_emby_sessions(payload, server_id="server", release_grace_seconds=30)
        first = list_reservations()[0]
        first_seen = list_emby_bindings()[0].last_observed_at
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"), server_id="server", release_grace_seconds=30)
        reservations = list_reservations()
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0].reservation_id, first.reservation_id)
        self.assertEqual(reservations[0].last_confirmation_source, "emby_playback_heartbeat")
        self.assertGreaterEqual(list_emby_bindings()[0].last_observed_at, first_seen)

    def test_live_playback_reuses_compatible_provisional_runtime_reservation(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        from app.services.logs import list_logs
        provisional = resolve_source("live_one", "channel", client_session="live-session-1",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=30)
        reservations = list_reservations()
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0].reservation_id, provisional.reservation_id)
        self.assertEqual(reservations[0].lifecycle_state, "active")
        messages = [row.message for row in list_logs()]
        self.assertTrue(any("emby_reservation_reused" in message for message in messages))
        self.assertTrue(any("emby_reservation_promoted" in message for message in messages))
        self.assertTrue(any("emby_binding_created" in message for message in messages))

    def test_incompatible_provisional_is_ignored_for_fresh_explicit_session(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        incompatible = resolve_source("live_one", "channel", client_fingerprint="vlc-runtime",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=30)
        rows = list_reservations()
        original = next(row for row in rows if row.reservation_id == incompatible.reservation_id)
        emby = next(row for row in rows if row.identity_type == "explicit_session")
        self.assertEqual(original.lifecycle_state, "provisional")
        self.assertEqual((emby.catalog_item_id, emby.lifecycle_state), ("live_one", "active"))

    def test_emby_created_live_reservation_releases_after_grace(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=5)
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=5)
        old = (datetime.utcnow() - timedelta(seconds=6)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE emby_playback_bindings SET missing_since=? WHERE released_at IS NULL", (old,))
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=5)
        self.assertEqual(list_reservations()[0].lifecycle_state, "released")

    def test_reserving_runtime_get_records_capacity_neutral_observation(self):
        from app.main import app
        client = TestClient(app)
        response = client.get("/r/movie/movie_one", headers={
            "User-Agent": "Emby/4.9", "X-Emby-Device-Id": "device-1",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM runtime_correlation_observations").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["catalog_item_id"], rows[0]["media_type"], rows[0]["request_profile"]),
                         ("movie_one", "movie", "emby_server"))
        self.assertNotIn("device-1", json.dumps(dict(rows[0])))

    def test_existing_binding_reuse_when_later_payload_has_no_runtime_url(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reservation = self._reservation()
        reconcile_emby_sessions(normalize_emby_sessions(self._payload(), "server"),
                                server_id="server", release_grace_seconds=30)
        sessions = normalize_emby_sessions(self._payload_without_runtime_url(), "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        self.assertEqual((matched, unmatched), (0, 1))
        self.assertIsNone(sessions[0].reservation_id)
        self.assertEqual(sessions[0].unmatched_reason, "catalog_identity_unresolved")

    def test_unique_recent_emby_runtime_observation_binds_without_original_url(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reservation = self._reservation()
        before = get_status().consuming_reservations
        self._observation(reservation)
        sessions = normalize_emby_sessions(self._payload_without_runtime_url(), "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        current = next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id)
        self.assertEqual((matched, unmatched), (0, 1))
        self.assertEqual(sessions[0].correlation_method, "catalog_identity_unresolved")
        self.assertFalse(sessions[0].recent_runtime_observation_found)
        self.assertEqual(sessions[0].correlation_candidate_count, 0)
        self.assertEqual(current.lifecycle_state, "provisional")
        self.assertEqual(get_status().consuming_reservations, before)

    def test_multiple_recent_runtime_observations_are_rejected_as_ambiguous(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        first = self._reservation("movie_one", session="first")
        second = self._reservation("movie_two", session="second")
        self._observation(first)
        self._observation(second)
        sessions = normalize_emby_sessions(self._payload_without_runtime_url(), "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        self.assertEqual((matched, unmatched), (0, 1))
        self.assertEqual(sessions[0].unmatched_reason, "catalog_identity_unresolved")
        self.assertEqual(sessions[0].correlation_candidate_count, 0)
        self.assertTrue(all(row.lifecycle_state == "provisional" for row in list_reservations()))

    def test_ip_or_title_similarity_alone_does_not_bind(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reservation = self._reservation()
        self._observation(reservation, profile="generic_http_client", stable=None,
                          address="same-address")
        sessions = normalize_emby_sessions(
            self._payload_without_runtime_url(name="movie_one"), "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        self.assertEqual((matched, unmatched), (0, 1))
        self.assertEqual(sessions[0].unmatched_reason, "catalog_identity_unresolved")
        self.assertEqual(sessions[0].rejected_for_client_context_count, 0)
        current = next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id)
        self.assertEqual(current.lifecycle_state, "provisional")

    def test_expired_runtime_observation_does_not_bind_and_reports_age(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reservation = self._reservation()
        self._observation(reservation)
        old = (datetime.utcnow() - timedelta(minutes=3)).isoformat()
        future = (datetime.utcnow() + timedelta(minutes=1)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE runtime_correlation_observations SET observed_at=?,expires_at=?", (old, future))
        sessions = normalize_emby_sessions(self._payload_without_runtime_url(), "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        self.assertEqual((matched, unmatched), (0, 1))
        self.assertEqual(sessions[0].unmatched_reason, "catalog_identity_unresolved")
        self.assertEqual(sessions[0].rejected_for_age_count, 0)

    def test_emby_promotes_provisional_without_extra_capacity(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reservation = self._reservation()
        before = get_status().consuming_reservations
        sessions = normalize_emby_sessions(self._payload(), "server")
        matched, unmatched = reconcile_emby_sessions(sessions, server_id="server", release_grace_seconds=30)
        current = next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id)
        self.assertEqual((matched, unmatched), (1, 0))
        self.assertEqual(current.lifecycle_state, "active")
        self.assertEqual(current.promotion_reason, "emby_playback_confirmed")
        self.assertEqual(current.last_confirmation_source, "emby_playback_confirmed")
        self.assertEqual(get_status().consuming_reservations, before)

    def test_active_and_paused_playback_renew_same_reservation(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reservation = self._reservation()
        reconcile_emby_sessions(normalize_emby_sessions(self._payload(), "server"), server_id="server", release_grace_seconds=30)
        first = next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id)
        old_expiry = first.active_expires_at
        reconcile_emby_sessions(normalize_emby_sessions(self._payload(paused=True), "server"), server_id="server", release_grace_seconds=30)
        current = next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id)
        self.assertEqual(current.lifecycle_state, "active")
        self.assertEqual(current.last_confirmation_source, "emby_playback_heartbeat")
        self.assertGreaterEqual(current.active_expires_at, old_expiry)
        self.assertEqual(get_status().consuming_reservations, 1)

    def test_missing_grace_reappearance_and_confirmed_release(self):
        from app.services.emby import list_emby_bindings, normalize_emby_sessions, reconcile_emby_sessions
        reservation = self._reservation()
        observed = normalize_emby_sessions(self._payload(), "server")
        reconcile_emby_sessions(observed, server_id="server", release_grace_seconds=30)
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=30)
        self.assertIsNotNone(list_emby_bindings()[0].missing_since)
        self.assertEqual(next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id).lifecycle_state, "active")
        reconcile_emby_sessions(observed, server_id="server", release_grace_seconds=30)
        self.assertIsNone(list_emby_bindings()[0].missing_since)
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=30)
        old = (datetime.utcnow() - timedelta(seconds=31)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE emby_playback_bindings SET missing_since=? WHERE released_at IS NULL", (old,))
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=30)
        current = next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id)
        self.assertEqual((current.lifecycle_state, current.release_reason), ("released", "emby_session_disappeared"))

    def test_failed_poll_does_not_mark_missing_or_release(self):
        from app.services.emby import EmbyError, list_emby_bindings, normalize_emby_sessions, poll_emby_once, reconcile_emby_sessions, update_emby_settings
        reservation = self._reservation()
        reconcile_emby_sessions(normalize_emby_sessions(self._payload(), "server"), server_id="server", release_grace_seconds=5)
        update_emby_settings(EmbySettingsUpdate(enabled=True, server_url="http://emby", api_key="key"))
        with patch("app.services.emby._request_json", side_effect=EmbyError("Emby could not be reached before the request timeout.", "degraded")):
            status = poll_emby_once()
        self.assertEqual(status.health_state, "degraded")
        self.assertIsNone(list_emby_bindings()[0].missing_since)
        self.assertEqual(next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id).lifecycle_state, "active")

    def test_successful_poll_recovers_after_failure(self):
        from app.services.emby import EmbyError, poll_emby_once, update_emby_settings
        reservation = self._reservation()
        update_emby_settings(EmbySettingsUpdate(enabled=True, server_url="http://emby", api_key="key"))
        with patch("app.services.emby._request_json", side_effect=EmbyError("Emby is unavailable.", "degraded")):
            self.assertEqual(poll_emby_once().consecutive_failures, 1)
        with patch("app.services.emby._request_json", side_effect=[
            {"Id": "server", "ServerName": "Home", "Version": "4.8"}, self._payload()]):
            status = poll_emby_once()
        self.assertEqual((status.health_state, status.consecutive_failures, status.matched_playback_count), ("healthy", 0, 1))
        current = next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id)
        self.assertEqual(current.lifecycle_state, "active")

    def test_outage_time_does_not_advance_pending_release_grace(self):
        from app.services.emby import EmbyError, list_emby_bindings, normalize_emby_sessions, poll_emby_once, reconcile_emby_sessions, update_emby_settings
        reservation = self._reservation()
        reconcile_emby_sessions(normalize_emby_sessions(self._payload(), "server"), server_id="server", release_grace_seconds=30)
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=30)
        old = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE emby_playback_bindings SET missing_since=? WHERE released_at IS NULL", (old,))
        update_emby_settings(EmbySettingsUpdate(enabled=True, server_url="http://emby", api_key="key"))
        with patch("app.services.emby._request_json", side_effect=EmbyError("Emby is unavailable.", "degraded")):
            poll_emby_once()
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, []]):
            poll_emby_once()
        binding = list_emby_bindings()[0]
        current = next(row for row in list_reservations() if row.reservation_id == reservation.reservation_id)
        self.assertIsNone(binding.released_at)
        self.assertGreater(binding.missing_since, datetime.utcnow() - timedelta(seconds=5))
        self.assertEqual(current.lifecycle_state, "active")

    def test_ambiguous_and_unmatched_playback_do_not_mutate_broker(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        self._reservation(session="one")
        self._reservation(session="two")
        before = [(row.reservation_id, row.lifecycle_state) for row in list_reservations()]
        matched, unmatched = reconcile_emby_sessions(normalize_emby_sessions(self._payload(), "server"), server_id="server", release_grace_seconds=30)
        after = [(row.reservation_id, row.lifecycle_state) for row in list_reservations()]
        self.assertEqual((matched, unmatched), (0, 1))
        self.assertEqual(before, after)

    def test_item_change_releases_old_and_promotes_new(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reconcile_emby_sessions(normalize_emby_sessions(self._payload(), "server"), server_id="server", release_grace_seconds=30)
        old = next(row for row in list_reservations() if row.catalog_item_id == "movie_one" and row.lifecycle_state == "active")
        reconcile_emby_sessions(normalize_emby_sessions(self._payload("movie_two"), "server"), server_id="server", release_grace_seconds=30)
        rows = {row.reservation_id: row for row in list_reservations()}
        self.assertEqual(rows[old.reservation_id].lifecycle_state, "released")
        self.assertEqual(len([row for row in rows.values() if row.catalog_item_id == "movie_two" and row.lifecycle_state == "active"]), 1)

    def test_api_redaction_bounds_and_disabled_status(self):
        from app.main import app
        client = TestClient(app)
        response = client.put("/api/integrations/emby", json={"server_url": "http://emby", "api_key": "do-not-return"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("do-not-return", response.text)
        self.assertNotIn("api_key", response.json())
        self.assertEqual(client.get("/api/integrations/emby/status").json()["health_state"], "disabled")
        self.assertEqual(client.get("/api/integrations/emby/sessions?limit=501").status_code, 422)

    def test_poller_prevents_overlap_and_stops_cleanly(self):
        from app.services.emby_poller import EmbyPoller
        poller = EmbyPoller()

        async def scenario():
            poller._poll_lock = asyncio.Lock()
            await poller._poll_lock.acquire()
            try:
                self.assertFalse(await poller.poll_once())
            finally:
                poller._poll_lock.release()
            with patch("app.services.emby_poller.get_emby_settings") as settings:
                settings.return_value.enabled = False
                settings.return_value.poll_interval_seconds = 5
                await poller.start()
                self.assertTrue(poller.running)
                await poller.stop()
                self.assertFalse(poller.running)
        asyncio.run(scenario())

    def test_migration_is_additive_and_idempotent(self):
        from app.services.emby import ensure_emby_schema
        reservation = self._reservation()
        ensure_emby_schema()
        ensure_emby_schema()
        rows = {row.reservation_id: row for row in list_reservations()}
        self.assertIn(reservation.reservation_id, rows)
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("emby_playback_bindings", tables)
        self.assertIn("emby_observed_sessions", tables)
        self.assertIn("runtime_correlation_observations", tables)


if __name__ == "__main__":
    unittest.main()
