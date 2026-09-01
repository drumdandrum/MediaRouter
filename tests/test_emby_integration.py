import asyncio
from concurrent.futures import ThreadPoolExecutor
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
from app.db.migrations import migrate_database
from app.schemas.integrations import EmbySettingsUpdate
from app.schemas.providers import AccountCreate, ProviderCreate
from app.services.broker import (
    force_expire_reservation, get_status,
    list_reservations, release_reservation, resolve_source,
)
from app.services.providers import create_account, create_provider
from pydantic import ValidationError


class EmbyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["MEDIA_ROUTER_DATA_DIR"] = str(Path(self.temp.name) / "data")
        get_settings.cache_clear()
        migrate_database()
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

    def _vod_payload(self, *, item_id="emby-movie", media_type="movie", session="vod-session",
                     media_source="vod-media-source"):
        item_type = "Movie" if media_type == "movie" else "Episode"
        return [{"Id": session, "DeviceId": "vod-device", "DeviceName": "Lab",
            "Client": "Emby Web", "UserId": "vod-user", "UserName": "Viewer",
            "NowPlayingItem": {"Id": item_id, "Name": "Untrusted title", "Type": item_type,
                "Path": f"/media/{item_id}.strm"},
            "PlayState": {"MediaSourceId": media_source, "PositionTicks": 1000,
                "IsPaused": False}}]

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
                (emby_server_id,integration_id,emby_item_id,emby_media_source_id,emby_channel_name,catalog_item_id,mapping_source,created_at,updated_at)
                VALUES ('server','server',?,?,?,?, 'manual',?,?)""",
                (item_id, media_source, catalog_id, catalog_id, now, now))

    def _placement(self, catalog_id, display_title, index=0, active=1):
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("""INSERT INTO channel_placements
                (catalog_item_id,source_identity,source_name,source_playlist,display_title,
                 placement_index,active,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (catalog_id, f"source-{catalog_id}-{index}", "Test", "test.m3u",
                 display_title, index, active, now, now))

    def _catalog_item(self, internal_id, media_type, title, *,
                      show_name=None, season=None, episode=None, parent=None):
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("""INSERT INTO catalog_items
                (internal_id,media_type,title,normalized_title,show_name,season_number,
                 episode_number,parent_internal_id,confidence,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?, 'high',?,?)""",
                (internal_id, media_type, title, title.strip().lower(), show_name,
                 season, episode, parent, now, now))

    def _mapping_audit(self, media_types, *, channels=None, items=None):
        from app.schemas.integrations import EmbyMappingAuditRequest
        from app.services.emby_audit import preview_emby_mapping_audit
        responses = [{"Id": "server"}]
        if "channel" in media_types:
            channel_items = channels or []
            responses.append({"Items": channel_items,
                              "TotalRecordCount": len(channel_items)})
        if any(media_type in {"movie", "series", "episode"}
               for media_type in media_types):
            vod_items = items or []
            responses.append({"Items": vod_items,
                              "TotalRecordCount": len(vod_items)})
        configured = {"server_url": "http://emby", "api_key": "key",
                      "request_timeout_seconds": 10, "verify_tls": True}
        with patch("app.services.emby._private_settings", return_value=configured), \
             patch("app.services.emby._request_json", side_effect=responses):
            return preview_emby_mapping_audit(
                EmbyMappingAuditRequest(media_types=media_types)
            )

    def _captured_live_session_15769(self):
        fixture = Path(__file__).parent / "fixtures" / "emby_session_tvchannel_15769.json"
        return json.loads(fixture.read_text())

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

    def test_vod_mapping_crud_is_exact_server_scoped_and_validated(self):
        from app.main import app
        from app.services.emby import ensure_emby_schema
        ensure_emby_schema()
        ensure_emby_schema()
        self._catalog_item("series_one", "series", "Series One")
        client = TestClient(app)
        movie = "/api/integrations/emby/vod-item-mappings/server-a/emby-movie"
        created = client.put(movie, json={"catalog_id": "movie_one", "media_type": "movie",
                                          "emby_media_source_id": "source-a"})
        self.assertEqual(created.status_code, 200)
        self.assertEqual({key: created.json()[key] for key in
            ("integration_id", "emby_item_id", "catalog_item_id", "media_type", "mapping_source")}, {
            "integration_id": "server-a", "emby_item_id": "emby-movie",
            "catalog_item_id": "movie_one", "media_type": "movie", "mapping_source": "manual"})
        other = client.put(movie.replace("server-a", "server-b"),
                           json={"catalog_id": "movie_two", "media_type": "movie"})
        self.assertEqual(other.status_code, 200)
        listed = client.get("/api/integrations/emby/vod-item-mappings").json()
        self.assertEqual(len(listed), 2)
        self.assertEqual(client.get(movie).json()["catalog_item_id"], "movie_one")
        updated = client.put(movie, json={"catalog_id": "movie_two", "media_type": "movie"})
        self.assertEqual((updated.status_code, updated.json()["catalog_item_id"]), (200, "movie_two"))
        self.assertEqual(client.put(
            "/api/integrations/emby/vod-item-mappings/server-a/episode",
            json={"catalog_id": "episode_one", "media_type": "episode"}).status_code, 200)
        for payload in ({"catalog_id": "missing", "media_type": "movie"},
                        {"catalog_id": "live_one", "media_type": "movie"},
                        {"catalog_id": "series_one", "media_type": "movie"},
                        {"catalog_id": "movie_one", "media_type": "episode"}):
            self.assertIn(client.put(movie, json=payload).status_code, {400, 404})
        self.assertEqual(client.put("/api/integrations/emby/vod-item-mappings/server-a/" + "x" * 257,
                                    json={"catalog_id": "movie_one", "media_type": "movie"}).status_code, 400)
        self.assertEqual(client.delete(movie).status_code, 204)
        self.assertEqual(client.get(movie).status_code, 404)
        self.assertEqual(client.get(movie.replace("server-a", "server-b")).status_code, 200)
        self.assertEqual(client.get(
            "/api/integrations/emby/vod-item-mappings/server-a/episode").status_code, 200)
        self.assertEqual(client.delete(movie).status_code, 404)
        self.assertEqual(len(client.get("/api/integrations/emby/vod-item-mappings").json()), 2)
        client.close()

    def test_exact_vod_mapping_resolves_movie_and_episode_without_title_or_runtime_evidence(self):
        from app.services.emby import link_emby_vod_item, normalize_emby_sessions, reconcile_emby_sessions
        link_emby_vod_item("server", "emby-movie", "movie_one", "movie")
        link_emby_vod_item("server", "emby-episode", "episode_one", "episode")
        for item_id, media_type, catalog_id in (
                ("emby-movie", "movie", "movie_one"),
                ("emby-episode", "episode", "episode_one")):
            sessions = normalize_emby_sessions(self._vod_payload(
                item_id=item_id, media_type=media_type, session=f"session-{media_type}"), "server")
            original_open = Path.open
            def guarded_open(path, *args, **kwargs):
                if path.suffix == ".strm":
                    raise AssertionError("STRM files must not be opened")
                return original_open(path, *args, **kwargs)
            with patch.object(Path, "open", new=guarded_open):
                matched, unmatched = reconcile_emby_sessions(
                    sessions, server_id="server", release_grace_seconds=30)
            self.assertEqual((matched, unmatched, sessions[0].catalog_item_id), (1, 0, catalog_id))
            self.assertEqual(sessions[0].correlation_method, "emby_vod_item_mapping_manual")
        unmapped = normalize_emby_sessions(self._vod_payload(item_id="unmapped"), "server")
        self.assertEqual(reconcile_emby_sessions(
            unmapped, server_id="server", release_grace_seconds=30), (0, 1))
        self.assertEqual(unmapped[0].unmatched_reason, "catalog_identity_unresolved")
        wrong_server = normalize_emby_sessions(self._vod_payload(item_id="emby-movie"), "other-server")
        self.assertEqual(reconcile_emby_sessions(
            wrong_server, server_id="other-server", release_grace_seconds=30), (0, 1))

    def test_mapped_vod_adopts_exact_provisional_and_runs_binding_lifecycle(self):
        from app.services.emby import (delete_emby_vod_item_mapping, link_emby_vod_item,
            list_emby_bindings, normalize_emby_sessions, reconcile_emby_sessions)
        provisional = resolve_source("movie_one", "movie", client_fingerprint="runtime-fingerprint",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        before_capacity = get_status().consuming_reservations
        link_emby_vod_item("server", "emby-movie", "movie_one", "movie", "vod-media-source")
        payload = self._vod_payload()
        sessions = normalize_emby_sessions(payload, "server")
        self.assertEqual(reconcile_emby_sessions(
            sessions, server_id="server", release_grace_seconds=5), (1, 0))
        adopted = next(row for row in list_reservations() if row.reservation_id == provisional.reservation_id)
        self.assertEqual((adopted.reservation_id, adopted.source_availability_id, adopted.account_id),
                         (provisional.reservation_id, provisional.source_availability_id, provisional.account_id))
        self.assertEqual((adopted.identity_type, adopted.lifecycle_state,
                          adopted.promotion_reason),
                         ("explicit_session", "active", "emby_playback_confirmed"))
        self.assertEqual(get_status().consuming_reservations, before_capacity)
        binding = list_emby_bindings()[0]
        self.assertEqual((binding.emby_server_id, binding.emby_session_id, binding.emby_item_id,
                          binding.emby_media_source_id, binding.catalog_item_id, binding.media_type,
                          binding.reservation_id, binding.correlation_method),
                         ("server", "vod-session", "emby-movie", "vod-media-source", "movie_one",
                          "movie", provisional.reservation_id, "emby_vod_item_mapping_manual"))
        first_expiry = adopted.active_expires_at
        reconcile_emby_sessions(normalize_emby_sessions(payload, "server"),
                                server_id="server", release_grace_seconds=5)
        heartbeat = next(row for row in list_reservations() if row.reservation_id == provisional.reservation_id)
        self.assertEqual(heartbeat.last_confirmation_source, "emby_playback_heartbeat")
        self.assertGreaterEqual(heartbeat.active_expires_at, first_expiry)
        self.assertTrue(delete_emby_vod_item_mapping("server", "emby-movie"))
        self.assertEqual(reconcile_emby_sessions(normalize_emby_sessions(payload, "server"),
                                                server_id="server", release_grace_seconds=5), (1, 0))
        self.assertIsNone(list_emby_bindings()[0].released_at)
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=5)
        old = (datetime.utcnow() - timedelta(seconds=6)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE emby_playback_bindings SET missing_since=? WHERE released_at IS NULL", (old,))
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=5)
        terminal = next(row for row in list_reservations() if row.reservation_id == provisional.reservation_id)
        historical = list_emby_bindings()[0]
        self.assertEqual((terminal.lifecycle_state, terminal.release_reason),
                         ("released", "emby_session_disappeared"))
        self.assertIsNotNone(historical.released_at)
        self.assertEqual(historical.release_reason, "emby_session_disappeared")
        self.assertEqual(get_status().consuming_reservations, 0)
        future = normalize_emby_sessions(self._vod_payload(session="future-session"), "server")
        self.assertEqual(reconcile_emby_sessions(
            future, server_id="server", release_grace_seconds=5), (0, 1))
        self.assertEqual(future[0].unmatched_reason, "catalog_identity_unresolved")

    def test_mapped_episode_adopts_and_releases_exact_provisional(self):
        from app.services.emby import (link_emby_vod_item, list_emby_bindings,
            normalize_emby_sessions, reconcile_emby_sessions)
        provisional = resolve_source("episode_one", "episode",
            client_fingerprint="episode-runtime-fingerprint",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        link_emby_vod_item("server", "emby-episode", "episode_one", "episode",
                           "episode-media-source")
        payload = self._vod_payload(item_id="emby-episode", media_type="episode",
                                    session="episode-session",
                                    media_source="episode-media-source")
        sessions = normalize_emby_sessions(payload, "server")
        self.assertEqual(reconcile_emby_sessions(
            sessions, server_id="server", release_grace_seconds=5), (1, 0))
        adopted = next(row for row in list_reservations()
                       if row.reservation_id == provisional.reservation_id)
        binding = list_emby_bindings()[0]
        self.assertEqual((adopted.lifecycle_state, adopted.identity_type,
                          binding.catalog_item_id, binding.media_type,
                          binding.correlation_method),
                         ("active", "explicit_session", "episode_one", "episode",
                          "emby_vod_item_mapping_manual"))
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=5)
        old = (datetime.utcnow() - timedelta(seconds=6)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE emby_playback_bindings SET missing_since=? WHERE released_at IS NULL",
                         (old,))
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=5)
        terminal = next(row for row in list_reservations()
                        if row.reservation_id == provisional.reservation_id)
        historical = list_emby_bindings()[0]
        self.assertEqual((terminal.lifecycle_state, terminal.release_reason,
                          historical.release_reason, get_status().consuming_reservations),
                         ("released", "emby_session_disappeared",
                          "emby_session_disappeared", 0))

    def test_vod_mapping_does_not_change_channel_resolution_or_make_runtime_observations_authoritative(self):
        from app.services.emby import (link_emby_vod_item, normalize_emby_sessions,
                                       reconcile_emby_sessions)
        link_emby_vod_item("server", "same-item", "movie_one", "movie")
        live = self._mapped_live_payload("same-item", "live_one", "live-session", "live-source")
        self._channel_mapping("same-item", "live_one", "live-source")
        live_session = normalize_emby_sessions(live, "server")
        self.assertEqual(reconcile_emby_sessions(
            live_session, server_id="server", release_grace_seconds=30), (1, 0))
        self.assertEqual(live_session[0].catalog_item_id, "live_one")
        provisional = resolve_source("movie_two", "movie", client_fingerprint="runtime-only",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        self._observation(provisional)
        unresolved = normalize_emby_sessions(self._vod_payload(item_id="no-map"), "server")
        self.assertEqual(reconcile_emby_sessions(
            unresolved, server_id="server", release_grace_seconds=30), (0, 1))
        self.assertEqual(unresolved[0].unmatched_reason, "catalog_identity_unresolved")

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
                (emby_server_id,integration_id,emby_item_id,emby_media_source_id,emby_channel_name,catalog_item_id,mapping_source,created_at,updated_at)
                VALUES ('server','server','15747','source-opaque','24/7 EDDY MURPHY','live_one','manual',?,?)""", (now, now))
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

    def test_unique_placement_display_title_automatically_maps(self):
        from app.services.emby import list_emby_channel_mappings, refresh_emby_channel_mappings
        self._placement("live_one", "24/7 Comedy")
        channels = {"Items": [{"Id": "placement-title", "Name": "24/7 Comedy", "Type": "TvChannel"}]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            result = refresh_emby_channel_mappings()
        mapping = list_emby_channel_mappings()[0]
        self.assertEqual((result.mapped, mapping.catalog_item_id, mapping.mapping_source),
                         (1, "live_one", "automatic_placement_title"))

    def test_placement_title_html_entity_normalization_maps(self):
        from app.services.emby import preview_emby_channel_mappings
        self._placement("live_one", "Rock &amp;   Roll")
        channels = {"Items": [{"Id": "html-title", "Name": "  ROCK & ROLL ", "Type": "TvChannel"}]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            item = preview_emby_channel_mappings().items[0]
        self.assertEqual((item.status, item.catalog_item_id, item.match_source),
                         ("automatic", "live_one", "automatic_placement_title"))

    def test_repeated_placements_for_one_catalog_id_remain_safe(self):
        from app.services.emby import preview_emby_channel_mappings
        self._placement("live_one", "Repeated Placement", 1)
        self._placement("live_one", "Repeated Placement", 2)
        channels = {"Items": [{"Id": "repeated-title", "Name": "Repeated Placement", "Type": "TvChannel"}]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            item = preview_emby_channel_mappings().items[0]
        self.assertEqual((item.status, item.catalog_item_id, item.match_source),
                         ("automatic", "live_one", "automatic_placement_title"))

    def test_duplicate_placement_titles_for_distinct_catalog_ids_are_ambiguous(self):
        from app.services.emby import preview_emby_channel_mappings
        self._placement("live_one", "Shared Placement", 1)
        self._placement("live_two", "Shared Placement", 2)
        channels = {"Items": [{"Id": "shared-title", "Name": "Shared Placement", "Type": "TvChannel"}]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            item = preview_emby_channel_mappings().items[0]
        self.assertEqual((item.status, item.catalog_item_id), ("ambiguous", None))

    def test_manual_mapping_wins_over_placement_title_mapping(self):
        from app.services.emby import link_emby_channel, preview_emby_channel_mappings
        self._placement("live_two", "Manual Wins")
        link_emby_channel("server", "manual-placement", "live_one")
        channels = {"Items": [{"Id": "manual-placement", "Name": "Manual Wins", "Type": "TvChannel"}]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            item = preview_emby_channel_mappings().items[0]
        self.assertEqual((item.status, item.catalog_item_id, item.match_source),
                         ("manual", "live_one", "manual"))

    def test_marker_mapping_wins_over_placement_title_mapping(self):
        from app.services.emby import preview_emby_channel_mappings
        self._placement("live_two", "Marker Wins")
        channels = {"Items": [{"Id": "marker-placement", "Name": "Marker Wins", "Type": "TvChannel",
            "ProviderIds": {"TvgId": "mr:live_one"}}]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            item = preview_emby_channel_mappings().items[0]
        self.assertEqual((item.status, item.catalog_item_id, item.match_source),
                         ("automatic", "live_one", "automatic_marker"))

    def test_duplicate_emby_lineup_names_make_placement_title_ambiguous(self):
        from app.services.emby import preview_emby_channel_mappings
        self._placement("live_one", "Duplicate Emby Name")
        channels = {"Items": [
            {"Id": "duplicate-emby-a", "Name": "Duplicate Emby Name", "Type": "TvChannel"},
            {"Id": "duplicate-emby-b", "Name": "Duplicate Emby Name", "Type": "TvChannel"},
        ]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            preview = preview_emby_channel_mappings()
        self.assertTrue(all(item.status == "ambiguous" and item.catalog_item_id is None
                            for item in preview.items))

    def test_captured_sparse_tvchannel_maps_only_after_refresh_persists_crosswalk(self):
        from app.services.emby import (
            list_emby_channel_mappings, normalize_emby_sessions,
            reconcile_emby_sessions, refresh_emby_channel_mappings,
        )
        payload = self._captured_live_session_15769()
        sessions = normalize_emby_sessions(payload, "server")
        matched, unmatched = reconcile_emby_sessions(
            sessions, server_id="server", release_grace_seconds=30)
        self.assertEqual((matched, unmatched, sessions[0].unmatched_reason),
                         (0, 1, "catalog_identity_unresolved"))
        self.assertEqual(len(list_reservations()), 0)

        self._placement("live_one", "24/7 BRUCE LEE MOVIES")
        channels = {"Items": [payload[0]["NowPlayingItem"] | {
            "MediaSources": [{"Id": payload[0]["PlayState"]["MediaSourceId"]}]
        }]}
        with patch("app.services.emby._request_json", side_effect=[{"Id": "server"}, channels]):
            refresh_emby_channel_mappings()
        mapping = list_emby_channel_mappings()[0]
        self.assertEqual(
            (mapping.emby_item_id, mapping.emby_media_source_id,
             mapping.catalog_item_id, mapping.mapping_source),
            ("15769", "a952eb5529de17e017ad5b97cce9f424",
             "live_one", "automatic_placement_title"),
        )

        sessions = normalize_emby_sessions(payload, "server")
        matched, unmatched = reconcile_emby_sessions(
            sessions, server_id="server", release_grace_seconds=30)
        self.assertEqual((matched, unmatched, sessions[0].catalog_item_id), (1, 0, "live_one"))
        self.assertEqual(len(list_reservations()), 1)

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

    def test_mapping_audit_request_validation_and_api_failure_boundaries(self):
        from app.main import app
        from app.schemas.integrations import EmbyMappingAuditRequest
        from app.services.emby import EmbyError
        for values in (
            {"media_types": ["invalid"]},
            {"media_types": []},
            {"media_types": ["movie", "movie"]},
            {"offset": -1},
            {"limit": 0},
            {"limit": 501},
        ):
            with self.assertRaises(ValidationError):
                EmbyMappingAuditRequest(**values)
        client = TestClient(app)
        self.assertEqual(client.post(
            "/api/integrations/emby/mapping-audit/preview",
            json={"limit": 501},
        ).status_code, 422)
        not_configured = client.post(
            "/api/integrations/emby/mapping-audit/preview",
            json={"media_types": ["movie"]},
        )
        self.assertEqual(not_configured.status_code, 409)
        configured = {"server_url": "http://emby", "api_key": "key",
                      "request_timeout_seconds": 10, "verify_tls": True}
        with patch("app.services.emby._private_settings",
                   return_value=configured), \
             patch("app.services.emby._request_json", side_effect=[
                 {"Id": "server"}, {"Items": [], "TotalRecordCount": 0},
             ]):
            empty = client.post(
                "/api/integrations/emby/mapping-audit/preview",
                json={"media_types": ["movie"]},
            )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual((empty.json()["scanned_count"],
                          empty.json()["scan_complete"],
                          empty.json()["truncated"]), (0, True, False))
        with patch("app.api.integrations.preview_emby_mapping_audit",
                   side_effect=EmbyError("credential-bearing detail", "degraded")):
            failed = client.post(
                "/api/integrations/emby/mapping-audit/preview",
                json={"media_types": ["movie"]},
            )
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn("credential-bearing", failed.text)

    def test_mapping_audit_uses_bounded_paging_and_reports_truncation(self):
        from app.schemas.integrations import EmbyMappingAuditRequest
        from app.services.emby_audit import preview_emby_mapping_audit
        configured = {"server_url": "http://emby", "api_key": "key",
                      "request_timeout_seconds": 10, "verify_tls": True}
        first = [{"Id": "m1", "Name": "One", "Type": "Movie"},
                 {"Id": "m2", "Name": "Two", "Type": "Movie"}]
        second = [{"Id": "m3", "Name": "Three", "Type": "Movie"}]
        with patch("app.services.emby._private_settings", return_value=configured), \
             patch("app.services.emby._request_json", side_effect=[
                 {"Id": "server"},
                 {"Items": first, "TotalRecordCount": 4},
                 {"Items": second, "TotalRecordCount": 4},
             ]) as request, \
             patch("app.services.emby_audit.EMBY_AUDIT_PAGE_SIZE", 2), \
             patch("app.services.emby_audit.EMBY_AUDIT_SCAN_CAP", 3):
            result = preview_emby_mapping_audit(EmbyMappingAuditRequest(
                media_types=["movie"], offset=1, limit=1,
            ))
        self.assertEqual((result.scanned_count, result.total_details,
                          result.returned_count), (3, 3, 1))
        self.assertTrue(result.truncated)
        self.assertFalse(result.scan_complete)
        self.assertIn("StartIndex=0", request.call_args_list[1].args[0])
        self.assertIn("Limit=2", request.call_args_list[1].args[0])
        self.assertIn("StartIndex=2", request.call_args_list[2].args[0])
        self.assertIn("Limit=1", request.call_args_list[2].args[0])

    def test_mapping_audit_groups_manual_exact_and_unmatched_results(self):
        from app.services.emby import link_emby_channel
        link_emby_channel("server", "manual-channel", "live_one")
        channels = [
            {"Id": "manual-channel", "Name": "Manual", "Type": "TvChannel",
             "ProviderIds": {"MediaRouter": "mr:live_two"}},
            {"Id": "marker-channel", "Name": "Marker", "Type": "TvChannel",
             "Path": "http://router/r/live/live_two?token=not-returned"},
            {"Id": "unknown-channel", "Name": "Unknown", "Type": "TvChannel",
             "Path": "/private/provider/path"},
        ]
        result = self._mapping_audit(["channel"], channels=channels)
        by_id = {item.emby_item_id: item for item in result.items}
        self.assertEqual((by_id["manual-channel"].classification,
                          by_id["manual-channel"].evidence_source,
                          by_id["manual-channel"].catalog_item_id),
                         ("manual", "manual_mapping", "live_one"))
        self.assertEqual((by_id["marker-channel"].classification,
                          by_id["marker-channel"].evidence_source,
                          by_id["marker-channel"].catalog_item_id),
                         ("exact", "durable_marker", "live_two"))
        self.assertEqual(by_id["unknown-channel"].classification, "unmatched")
        self.assertEqual(result.classification_counts["exact"], 1)
        self.assertEqual(result.classification_counts["manual"], 1)
        self.assertEqual(result.classification_counts["unmatched"], 1)
        self.assertEqual(result.classification_counts["unsupported"], 0)
        self.assertEqual(result.evidence_source_counts["durable_marker"], 1)
        self.assertEqual(result.evidence_source_counts["manual_mapping"], 1)
        self.assertEqual(result.evidence_source_counts["persisted_item_id"], 0)
        self.assertEqual(result.collision_totals.conflicting_exact_evidence, 1)

    def test_mapping_audit_normalizes_titles_and_scopes_movies(self):
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("""UPDATE catalog_items
                SET title='Rock &amp; Roll',normalized_title='rock &amp; roll'
                WHERE internal_id='live_one'""")
            conn.execute("""UPDATE catalog_items
                SET title='Rock & Roll',normalized_title='rock & roll'
                WHERE internal_id='movie_one'""")
        channels = [{"Id": "opaque-channel", "Name": "  ROCK & ROLL ",
                     "Type": "TvChannel"}]
        movies = [{"Id": "opaque-movie", "Name": "Rock &amp;   Roll",
                   "Type": "Movie"}]
        result = self._mapping_audit(
            ["channel", "movie"], channels=channels, items=movies,
        )
        by_type = {item.media_type: item for item in result.items}
        self.assertEqual((by_type["channel"].classification,
                          by_type["channel"].catalog_item_id),
                         ("normalized_title", "live_one"))
        self.assertEqual((by_type["movie"].classification,
                          by_type["movie"].catalog_item_id),
                         ("normalized_title", "movie_one"))
        self.assertTrue(by_type["channel"].apply_eligible)
        self.assertFalse(by_type["movie"].apply_eligible)
        self.assertEqual(by_type["movie"].ineligibility_reason,
                         "vod_application_not_supported")

    def test_mapping_audit_reports_title_and_placement_collisions(self):
        self._catalog_item("movie_duplicate", "movie", "Duplicate Movie")
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("""UPDATE catalog_items
                SET title='Duplicate Movie',normalized_title='duplicate movie'
                WHERE internal_id='movie_one'""")
        self._placement("live_one", "Repeated Placement", 10)
        self._placement("live_one", "Repeated Placement", 11)
        self._placement("live_one", "Shared Placement", 12)
        self._placement("live_two", "Shared Placement", 13)
        channels = [
            {"Id": "repeat", "Name": "Repeated Placement", "Type": "TvChannel"},
            {"Id": "collision", "Name": "Shared Placement", "Type": "TvChannel"},
            {"Id": "duplicate-a", "Name": "Duplicate Emby", "Type": "TvChannel"},
            {"Id": "duplicate-b", "Name": "Duplicate Emby", "Type": "TvChannel"},
        ]
        movies = [
            {"Id": "movie-duplicate", "Name": "Duplicate Movie", "Type": "Movie"},
            {"Id": "movie-placement-only", "Name": "Repeated Placement",
             "Type": "Movie"},
        ]
        result = self._mapping_audit(
            ["channel", "movie"], channels=channels, items=movies,
        )
        by_id = {item.emby_item_id: item for item in result.items}
        self.assertEqual((by_id["repeat"].classification,
                          by_id["repeat"].catalog_item_id,
                          by_id["repeat"].evidence_source),
                         ("placement_title", "live_one", "active_placement_title"))
        self.assertEqual(by_id["collision"].classification, "ambiguous")
        self.assertTrue(all(by_id[item].classification == "ambiguous"
                            for item in ("duplicate-a", "duplicate-b")))
        self.assertEqual(by_id["movie-duplicate"].classification, "ambiguous")
        self.assertEqual(by_id["movie-placement-only"].classification, "unmatched")
        self.assertIsNone(by_id["movie-placement-only"].evidence_source)
        self.assertGreaterEqual(result.collision_totals.duplicate_emby_names, 2)
        self.assertGreaterEqual(result.collision_totals.duplicate_catalog_titles, 1)
        self.assertGreaterEqual(result.collision_totals.placement_title_collisions, 1)

    def test_mapping_audit_series_and_structural_episode_matching(self):
        self._catalog_item("series_show", "series", "My Show")
        self._catalog_item("episode_structural", "episode", "Pilot",
                           show_name="My Show", season=1, episode=2,
                           parent="series_show")
        self._catalog_item("episode_structural_duplicate", "episode", "Other Cut",
                           show_name="Duplicated Show", season=3, episode=4)
        self._catalog_item("episode_structural_duplicate_2", "episode", "Other Cut 2",
                           show_name="Duplicated Show", season=3, episode=4)
        items = [
            {"Id": "series-opaque", "Name": "MY SHOW", "Type": "Series"},
            {"Id": "episode-opaque", "Name": "Any Episode Title", "Type": "Episode",
             "SeriesName": "My Show", "ParentIndexNumber": 1, "IndexNumber": 2},
            {"Id": "episode-title-only", "Name": "Pilot", "Type": "Episode"},
            {"Id": "episode-duplicate", "Name": "Cut", "Type": "Episode",
             "SeriesName": "Duplicated Show", "ParentIndexNumber": 3,
             "IndexNumber": 4},
        ]
        result = self._mapping_audit(["series", "episode"], items=items)
        by_id = {item.emby_item_id: item for item in result.items}
        self.assertEqual((by_id["series-opaque"].classification,
                          by_id["series-opaque"].catalog_item_id),
                         ("normalized_title", "series_show"))
        self.assertEqual((by_id["episode-opaque"].classification,
                          by_id["episode-opaque"].evidence_source,
                          by_id["episode-opaque"].catalog_item_id),
                         ("exact", "structural_episode_identity",
                          "episode_structural"))
        self.assertEqual(
            (by_id["episode-title-only"].classification,
             by_id["episode-title-only"].ineligibility_reason),
            ("unmatched", "incomplete_episode_structure"),
        )
        self.assertEqual(by_id["episode-duplicate"].classification, "ambiguous")
        self.assertEqual(result.collision_totals.incomplete_episode_structure, 1)
        self.assertEqual(result.collision_totals.duplicate_episode_structures, 1)
        self.assertTrue(all(not item.apply_eligible for item in result.items))

    def test_mapping_audit_omits_unsupported_vod_crosswalk_evidence(self):
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("""INSERT INTO emby_channel_mappings
                (emby_server_id,integration_id,emby_item_id,emby_channel_name,
                 catalog_item_id,mapping_source,created_at,updated_at)
                VALUES ('server','server','shared-emby-id','Channel only',
                        'live_one','manual',?,?)""", (now, now))
        movie = {"Id": "shared-emby-id", "Name": "No Movie Match",
                 "Type": "Movie"}
        result = self._mapping_audit(["movie"], items=[movie])
        item = result.items[0]
        self.assertEqual(item.classification, "unmatched")
        self.assertIsNone(item.evidence_source)
        self.assertNotIn("persisted", item.model_dump_json())
        self.assertFalse(item.apply_eligible)

    def test_mapping_audit_is_read_only_and_sanitizes_diagnostics(self):
        from app.services.emby import refresh_emby_channel_mappings
        from app.services.emby_audit import _safe_text
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            before = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "emby_channel_mappings", "emby_observed_sessions",
                    "runtime_correlation_observations",
                )
            }
        movie = {
            "Id": "opaque",
            "Name": (
                "Movie http://user:password@example/path?token=secret "
                "rtsp://provider/private?access_token=secret "
                "/private/provider/path"
            ),
            "Type": "Movie",
            "Path": "http://provider/private?api_key=secret",
            "Overview": "token=secret",
        }
        with patch("app.services.emby.refresh_emby_channel_mappings",
                   wraps=refresh_emby_channel_mappings) as refresh:
            result = self._mapping_audit(["movie"], items=[movie])
        refresh.assert_not_called()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            after = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in before
            }
        self.assertEqual(before, after)
        serialized = result.model_dump_json()
        self.assertIn("[redacted-url]", serialized)
        self.assertIn("[redacted-path]", serialized)
        for secret in (
            "password", "token=secret", "access_token", "api_key",
            "rtsp://", "/private",
        ):
            self.assertNotIn(secret, serialized)
        unsafe_values = (
            "http://provider/path?token=secret",
            "https://provider/path?api_key=secret",
            "rtsp://provider/stream",
            "rtmp://provider/stream",
            "smb://server/share",
            "file:///private/movie.mkv",
            "ftp://provider/file",
            "udp://239.0.0.1:1234",
            "plugin:credential-bearing-value",
            "PlUgIn:mixed-case-value",
            "customscheme:opaque_value",
            "plugin&#58;html-escaped-value",
            "plugin&amp;#58;repeatedly-escaped-value",
            "http&#58;//provider/path?token=secret",
            "http&amp;#58;//provider/path?token=secret",
            "/var/lib/emby/movie.mkv",
            "&#47;var&#47;lib&#47;emby&#47;encoded.mkv",
            "Title (/var/lib/emby/embedded.mkv)",
            r"C:\Media\movie.mkv",
            r"Title (C:\Media\embedded.mkv)",
            r"\\server\share\movie.mkv",
            r"Title (\\server\share\embedded.mkv)",
        )
        for unsafe in unsafe_values:
            sanitized = _safe_text(f"Title {unsafe}")
            self.assertNotIn(unsafe, sanitized)
            self.assertIn("[redacted-", sanitized)
        safe_titles = (
            "Alien: Resurrection",
            "Face/Off",
            "Schindler's List",
            "Amélie 東京",
            "Season 1: Episode 2",
            "S01:E02",
            "10:30",
            "16:9",
            "John 3:16",
            "john:3",
            "go:home",
            "to:be",
            "URL: The Movie",
            "C: The Movie",
            "The file stream server plugin path URL",
        )
        for title in safe_titles:
            self.assertEqual(_safe_text(title), title)

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

    def test_unique_recent_provisional_is_adopted_without_capacity_change(self):
        from app.services.emby import list_emby_bindings, normalize_emby_sessions, reconcile_emby_sessions
        from app.services.logs import list_logs
        provisional = resolve_source("live_one", "channel", client_fingerprint="initial-runtime-request",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        original = (provisional.reservation_id, provisional.account_id,
                    provisional.source_availability_id, provisional.location_ref)
        self.assertEqual(get_status().consuming_reservations, 1)
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=30)
        reservations = list_reservations()
        self.assertEqual(len(reservations), 1)
        adopted = reservations[0]
        self.assertEqual(
            (adopted.reservation_id, adopted.account_id,
             adopted.source_availability_id, adopted.location_ref),
            original,
        )
        self.assertEqual((adopted.identity_type, adopted.lifecycle_state), ("explicit_session", "active"))
        self.assertEqual(get_status().consuming_reservations, 1)
        self.assertEqual(list_emby_bindings()[0].reservation_id, provisional.reservation_id)
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=5)
        heartbeat = next(row for row in list_reservations()
                         if row.reservation_id == provisional.reservation_id)
        self.assertEqual(heartbeat.last_confirmation_source, "emby_playback_heartbeat")
        self.assertEqual(get_status().consuming_reservations, 1)
        messages = [row.message for row in list_logs()]
        self.assertTrue(any("emby_provisional_adoption_succeeded" in message for message in messages))
        self.assertTrue(any("emby_reservation_promoted" in message for message in messages))
        self.assertTrue(any("emby_binding_created" in message for message in messages))
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=5)
        old = (datetime.utcnow() - timedelta(seconds=6)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE emby_playback_bindings SET missing_since=? WHERE released_at IS NULL",
                         (old,))
        reconcile_emby_sessions([], server_id="server", release_grace_seconds=5)
        released = next(row for row in list_reservations()
                        if row.reservation_id == provisional.reservation_id)
        self.assertEqual((released.lifecycle_state, released.release_reason),
                         ("released", "emby_session_disappeared"))

    def test_adoption_replaces_provisional_identity_and_alias_lifecycle_is_normal(self):
        import hashlib
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions

        provisional = resolve_source(
            "live_one", "channel", client_fingerprint="initial-runtime-request",
            origin_identity="runtime-origin", request_profile="runtime-profile",
            allow_reservation_reuse=True, lifecycle_enabled=True,
        ).reservation
        reconcile_emby_sessions(
            normalize_emby_sessions(self._live_payload(), "server"),
            server_id="server", release_grace_seconds=30,
        )
        expected_session = hashlib.sha256(
            b"explicit_session:live-session-1").hexdigest()[:32]
        expected_key = hashlib.sha256(
            f"live_one|channel|explicit_session|{expected_session}".encode()).hexdigest()[:40]
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT client_session,client_fingerprint,stable_client_id,
                          origin_identity_hash,request_profile,identity_type,
                          playback_identity_key
                   FROM broker_reservations WHERE reservation_id=?""",
                (provisional.reservation_id,),
            ).fetchone()
            aliases = conn.execute(
                """SELECT identity_type,identity_hash,active
                   FROM broker_reservation_identity_aliases
                   WHERE reservation_id=? ORDER BY first_seen_at""",
                (provisional.reservation_id,),
            ).fetchall()
        self.assertEqual(row["client_session"], expected_session)
        self.assertEqual(row["playback_identity_key"], expected_key)
        self.assertEqual(row["identity_type"], "explicit_session")
        self.assertIsNone(row["client_fingerprint"])
        self.assertIsNone(row["stable_client_id"])
        self.assertIsNone(row["origin_identity_hash"])
        self.assertIsNone(row["request_profile"])
        self.assertEqual(sum(alias["active"] for alias in aliases), 1)
        active_alias = next(alias for alias in aliases if alias["active"])
        self.assertEqual(
            (active_alias["identity_type"], active_alias["identity_hash"]),
            ("explicit_session", expected_session),
        )
        self.assertTrue(any(
            alias["identity_type"] == "derived_fingerprint" and not alias["active"]
            for alias in aliases
        ))

        release_reservation(provisional.reservation_id)
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            active_aliases = conn.execute(
                """SELECT COUNT(*) FROM broker_reservation_identity_aliases
                   WHERE reservation_id=? AND active=1""",
                (provisional.reservation_id,),
            ).fetchone()[0]
        self.assertEqual(active_aliases, 0)

    def test_no_provisional_candidate_falls_back_to_new_explicit_reservation(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=30)
        rows = list_reservations()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].identity_type, rows[0].lifecycle_state),
                         ("explicit_session", "active"))

    def test_multiple_recent_provisionals_are_ambiguous_and_fall_back(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        from app.services.logs import list_logs
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE accounts SET max_simultaneous_streams=3 WHERE id=?", (self.account.id,))
        first = resolve_source("live_one", "channel", client_fingerprint="runtime-a",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        second = resolve_source("live_one", "channel", client_fingerprint="runtime-b",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=30)
        rows = list_reservations()
        self.assertEqual(len(rows), 3)
        self.assertEqual({row.reservation_id for row in rows if row.lifecycle_state == "provisional"},
                         {first.reservation_id, second.reservation_id})
        self.assertEqual(len([row for row in rows if row.identity_type == "explicit_session"]), 1)
        messages = [row.message for row in list_logs()]
        self.assertTrue(any("emby_provisional_adoption_ambiguous" in message and
                            "candidate_count=2" in message for message in messages))
        self.assertTrue(any("emby_provisional_adoption_fallback" in message for message in messages))

    def test_provisional_outside_startup_window_is_not_adopted(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        stale = resolve_source("live_one", "channel", client_fingerprint="stale-runtime",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        old = (datetime.utcnow() - timedelta(minutes=3)).isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("UPDATE broker_reservations SET created_at=?,first_seen_at=? WHERE reservation_id=?",
                         (old, old, stale.reservation_id))
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=30)
        rows = {row.reservation_id: row for row in list_reservations()}
        self.assertEqual(rows[stale.reservation_id].lifecycle_state, "provisional")
        self.assertEqual(len([row for row in rows.values() if row.identity_type == "explicit_session"]), 1)

    def test_released_and_expired_provisionals_are_not_adopted(self):
        from app.services.emby import normalize_emby_sessions, reconcile_emby_sessions
        for terminal in ("released", "expired"):
            with self.subTest(terminal=terminal):
                provisional = resolve_source("live_one", "channel",
                    client_fingerprint=f"{terminal}-runtime",
                    allow_reservation_reuse=True, lifecycle_enabled=True).reservation
                if terminal == "released":
                    release_reservation(provisional.reservation_id)
                else:
                    force_expire_reservation(provisional.reservation_id)
                session = f"session-{terminal}"
                reconcile_emby_sessions(
                    normalize_emby_sessions(self._live_payload(session=session), "server"),
                    server_id="server", release_grace_seconds=30,
                )
                rows = {row.reservation_id: row for row in list_reservations()}
                self.assertEqual(rows[provisional.reservation_id].lifecycle_state, terminal)
                binding = next(binding for binding in
                    __import__("app.services.emby", fromlist=["list_emby_bindings"]).list_emby_bindings()
                    if binding.emby_session_id == session and binding.released_at is None)
                self.assertNotEqual(binding.reservation_id, provisional.reservation_id)

    def test_provisional_already_bound_to_another_session_is_not_adopted(self):
        from app.services.emby import list_emby_bindings, normalize_emby_sessions, reconcile_emby_sessions
        provisional = resolve_source("live_one", "channel", client_fingerprint="bound-runtime",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(get_settings().data_dir / "media_router.db") as conn:
            conn.execute("""INSERT INTO emby_playback_bindings
                (id,binding_key,emby_server_id,emby_session_id,reservation_id,catalog_item_id,
                 media_type,playback_state,first_observed_at,last_observed_at,
                 correlation_method,correlation_confidence,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'channel','playing',?,?,'test','authoritative',?,?)""",
                ("bound-id", "server:other-session", "server", "other-session",
                 provisional.reservation_id, "live_one", now, now, now, now))
        reconcile_emby_sessions(normalize_emby_sessions(self._live_payload(), "server"),
                                server_id="server", release_grace_seconds=30)
        current = next(binding for binding in list_emby_bindings()
                       if binding.emby_session_id == "live-session-1" and binding.released_at is None)
        self.assertNotEqual(current.reservation_id, provisional.reservation_id)

    def test_two_emby_sessions_cannot_share_one_adoptable_provisional(self):
        from app.services.emby import list_emby_bindings, normalize_emby_sessions, reconcile_emby_sessions
        provisional = resolve_source("live_one", "channel", client_fingerprint="shared-runtime",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        raw = self._live_payload(session="endpoint-a") + self._live_payload(session="endpoint-b")
        reconcile_emby_sessions(normalize_emby_sessions(raw, "server"),
                                server_id="server", release_grace_seconds=30)
        active_bindings = [binding for binding in list_emby_bindings() if binding.released_at is None]
        self.assertEqual(len(active_bindings), 2)
        self.assertEqual(len({binding.reservation_id for binding in active_bindings}), 2)
        self.assertIn(provisional.reservation_id,
                      {binding.reservation_id for binding in active_bindings})
        self.assertEqual(get_status().consuming_reservations, 2)

    def test_simultaneous_adoption_claims_are_atomic(self):
        from app.services.broker import adopt_provisional_reservation
        provisional = resolve_source("live_one", "channel", client_fingerprint="concurrent-runtime",
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda session: adopt_provisional_reservation(
                    "live_one", "channel", session, startup_window_seconds=90),
                ("simultaneous-a", "simultaneous-b"),
            ))
        self.assertEqual([result.status for result in results].count("adopted"), 1)
        self.assertEqual(sum(result.status in {"no_candidate", "race_lost"} for result in results), 1)
        adopted = next(result for result in results if result.status == "adopted")
        self.assertEqual(adopted.reservation.reservation_id, provisional.reservation_id)
        loser_session = ("simultaneous-a" if results[0].status != "adopted"
                         else "simultaneous-b")
        fallback = resolve_source("live_one", "channel", client_session=loser_session,
            allow_reservation_reuse=True, lifecycle_enabled=True).reservation
        self.assertNotEqual(fallback.reservation_id, provisional.reservation_id)
        self.assertEqual(get_status().consuming_reservations, 2)

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
