from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
import json
import re
import sqlite3
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, quote, unquote, urlsplit
from uuid import uuid4

from app.core.config import get_settings
from app.core.json_store import JsonStore
from app.schemas.integrations import (
    EmbyConnectionResult, EmbyIntegrationStatus, EmbyPlaybackBinding,
    EmbyPlaybackSession, EmbySettingsRead, EmbySettingsUpdate,
    EmbyChannelMapping, EmbyChannelRefreshResult, EmbyChannelMappingPreview,
    EmbyChannelMappingPreviewItem, EmbyChannelMappingPage,
)
from app.services.broker import BrokerUnavailable, confirm_reservation, heartbeat_reservation, release_reservation, resolve_source
from app.services.logs import add_log


RUNTIME_RE = re.compile(r"/r/(live|movie|episode)/([A-Za-z0-9_.:-]+)", re.IGNORECASE)
ROUTE_MEDIA_TYPES = {"live": "channel", "movie": "movie", "episode": "episode"}
TERMINAL_STATES = {"released", "expired", "superseded", "failed"}
CONSUMING_STATES = {"provisional", "active"}
RUNTIME_OBSERVATION_TTL_SECONDS = 120
EMBY_CORRELATION_WINDOW_SECONDS = 90


class EmbyError(Exception):
    def __init__(self, message: str, health_state: str = "degraded") -> None:
        super().__init__(message)
        self.health_state = health_state


def _now() -> datetime:
    return datetime.utcnow()


def _settings_store() -> JsonStore:
    return JsonStore(get_settings().data_dir / "emby_integration_settings.json", {
        "enabled": False, "server_url": "", "api_key": "", "poll_interval_seconds": 10,
        "release_grace_seconds": 30, "unavailable_timeout_seconds": 60,
        "request_timeout_seconds": 10, "verify_tls": True,
        "emby_runtime_correlation_enabled": False,
    })


def _status_store() -> JsonStore:
    return JsonStore(get_settings().data_dir / "emby_integration_status.json", {
        "health_state": "disabled", "last_poll_attempt": None, "last_successful_poll": None,
        "consecutive_failures": 0, "server_id": None, "server_name": None,
        "server_version": None, "observed_playback_count": 0,
        "matched_playback_count": 0, "unmatched_playback_count": 0, "last_error": None,
        "first_failure_at": None,
    })


def _private_settings() -> dict[str, Any]:
    return _settings_store().read()


def get_emby_settings() -> EmbySettingsRead:
    values = _private_settings()
    return EmbySettingsRead(**{key: value for key, value in values.items() if key != "api_key"},
                            has_api_key=bool(values.get("api_key")))


def update_emby_settings(payload: EmbySettingsUpdate) -> EmbySettingsRead:
    values = payload.model_dump(exclude_unset=True)
    if "api_key" in values and not values["api_key"]:
        values.pop("api_key")
    if "server_url" in values:
        values["server_url"] = (values["server_url"] or "").strip().rstrip("/")
    _settings_store().update(values)
    if payload.enabled is False:
        _status_store().update({"health_state": "disabled", "last_error": None})
    return get_emby_settings()


def _db_path() -> Path:
    return get_settings().data_dir / "media_router.db"


def _connect() -> sqlite3.Connection:
    from app.services.broker import _connect as broker_connect
    conn = broker_connect()
    ensure_emby_schema(conn)
    return conn


def ensure_emby_schema(conn: sqlite3.Connection | None = None) -> None:
    owns = conn is None
    if conn is None:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS emby_playback_bindings (
            id TEXT PRIMARY KEY,
            binding_key TEXT NOT NULL,
            emby_server_id TEXT NOT NULL,
            emby_session_id TEXT NOT NULL,
            emby_play_session_id TEXT,
            emby_device_id TEXT,
            emby_user_id TEXT,
            emby_item_id TEXT,
            emby_media_source_id TEXT,
            reservation_id TEXT NOT NULL,
            catalog_item_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            playback_state TEXT NOT NULL,
            first_observed_at TEXT NOT NULL,
            last_observed_at TEXT NOT NULL,
            last_confirmed_playing_at TEXT,
            missing_since TEXT,
            released_at TEXT,
            release_reason TEXT,
            correlation_method TEXT NOT NULL,
            correlation_confidence TEXT NOT NULL,
            device_name TEXT,
            client_name TEXT,
            user_name TEXT,
            item_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(reservation_id) REFERENCES broker_reservations(reservation_id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_emby_active_binding_key
            ON emby_playback_bindings(binding_key) WHERE released_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_emby_binding_reservation
            ON emby_playback_bindings(reservation_id, released_at);
        CREATE INDEX IF NOT EXISTS idx_emby_binding_missing
            ON emby_playback_bindings(missing_since, released_at);
        CREATE INDEX IF NOT EXISTS idx_emby_binding_observed
            ON emby_playback_bindings(last_observed_at DESC);
        CREATE TABLE IF NOT EXISTS emby_observed_sessions (
            binding_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runtime_correlation_observations (
            id TEXT PRIMARY KEY,
            reservation_id TEXT NOT NULL,
            catalog_item_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            route_type TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            request_identity_type TEXT,
            request_identity_hash TEXT,
            stable_emby_identifier_hash TEXT,
            request_profile TEXT,
            address_signature TEXT,
            user_agent_signature TEXT,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(reservation_id) REFERENCES broker_reservations(reservation_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_correlation_recent
            ON runtime_correlation_observations(observed_at DESC, media_type);
        CREATE INDEX IF NOT EXISTS idx_runtime_correlation_reservation
            ON runtime_correlation_observations(reservation_id, expires_at);
        CREATE TABLE IF NOT EXISTS emby_channel_mappings (
            emby_server_id TEXT NOT NULL,
            integration_id TEXT,
            emby_item_id TEXT NOT NULL,
            emby_media_source_id TEXT,
            emby_channel_name TEXT,
            catalog_item_id TEXT,
            mapping_source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(emby_server_id, emby_item_id),
            FOREIGN KEY(catalog_item_id) REFERENCES catalog_items(internal_id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_emby_channel_media_source
            ON emby_channel_mappings(emby_server_id, emby_media_source_id);
    """)
    mapping_columns = {row[1] for row in conn.execute("PRAGMA table_info(emby_channel_mappings)").fetchall()}
    if "integration_id" not in mapping_columns:
        conn.execute("ALTER TABLE emby_channel_mappings ADD COLUMN integration_id TEXT")
    conn.execute("UPDATE emby_channel_mappings SET integration_id=emby_server_id WHERE integration_id IS NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_emby_channel_integration_item ON emby_channel_mappings(integration_id,emby_item_id)")
    # Migrate legacy PlaySessionId-owned keys to the consumer lifecycle key.
    conn.execute("DROP INDEX IF EXISTS uq_emby_active_binding_key")
    duplicate_groups = conn.execute("""SELECT emby_server_id,emby_session_id FROM emby_playback_bindings
        WHERE released_at IS NULL GROUP BY emby_server_id,emby_session_id HAVING COUNT(*)>1""").fetchall()
    migration_now = _now().isoformat()
    for group in duplicate_groups:
        rows = conn.execute("""SELECT id,reservation_id FROM emby_playback_bindings WHERE emby_server_id=? AND emby_session_id=?
            AND released_at IS NULL ORDER BY last_observed_at DESC,id DESC""",
            (group["emby_server_id"], group["emby_session_id"])).fetchall()
        for redundant in rows[1:]:
            conn.execute("""UPDATE emby_playback_bindings SET released_at=?,release_reason='binding_key_migration',updated_at=?
                WHERE id=?""", (migration_now, migration_now, redundant["id"]))
            conn.execute("""UPDATE broker_reservations SET lifecycle_state='released',status='released',released_at=?,
                release_reason='binding_key_migration',last_action='reservation_released' WHERE reservation_id=?
                AND lifecycle_state IN ('provisional','active')""", (migration_now, redundant["reservation_id"]))
            conn.execute("UPDATE broker_reservation_identity_aliases SET active=0,last_seen_at=? WHERE reservation_id=?",
                         (migration_now, redundant["reservation_id"]))
    conn.execute("UPDATE emby_playback_bindings SET binding_key=emby_server_id || ':' || emby_session_id")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_emby_active_binding_key
        ON emby_playback_bindings(binding_key) WHERE released_at IS NULL""")
    conn.commit()
    if owns:
        conn.close()


def record_runtime_correlation_observation(
    *, reservation_id: str, catalog_item_id: str, media_type: str, route_type: str,
    request_identity_type: str | None, request_identity: str | None,
    stable_emby_identifier: str | None, request_profile: str | None,
    address_signature: str | None, user_agent_signature: str | None,
) -> None:
    """Persist capacity-neutral, privacy-safe evidence for a later Emby poll."""
    from app.services.broker import _identity_hash
    now = _now()
    normalized_media = ROUTE_MEDIA_TYPES.get(media_type, media_type)
    identity_hash = _identity_hash(request_identity, request_identity_type or "runtime_identity")
    stable_hash = _identity_hash(stable_emby_identifier, "stable_client_id")
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM runtime_correlation_observations WHERE expires_at<=?", (now.isoformat(),))
        conn.execute("""INSERT INTO runtime_correlation_observations
            (id,reservation_id,catalog_item_id,media_type,route_type,observed_at,
             request_identity_type,request_identity_hash,stable_emby_identifier_hash,
             request_profile,address_signature,user_agent_signature,expires_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uuid4().hex,reservation_id,catalog_item_id,normalized_media,route_type,now.isoformat(),
             request_identity_type,identity_hash,stable_hash,request_profile,address_signature,
             user_agent_signature,(now+timedelta(seconds=RUNTIME_OBSERVATION_TTL_SECONDS)).isoformat()))
        conn.commit()


def _sanitize_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, HTTPError):
        if exc.code in {401, 403}:
            return "authentication_failed", "Emby rejected the configured credentials."
        return "degraded", f"Emby returned HTTP {exc.code}."
    if isinstance(exc, (TimeoutError, URLError)):
        return "degraded", "Emby could not be reached before the request timeout."
    if isinstance(exc, (json.JSONDecodeError, TypeError, ValueError)):
        return "error", "Emby returned an unexpected response."
    return "error", "The Emby request failed."


def _request_json(path: str, settings: dict[str, Any] | None = None) -> Any:
    settings = settings or _private_settings()
    if not settings.get("server_url") or not settings.get("api_key"):
        raise EmbyError("Emby server URL and API key are required.", "not_configured")
    request = Request(f"{settings['server_url'].rstrip('/')}{path}", headers={
        "Accept": "application/json", "X-Emby-Token": settings["api_key"],
        "User-Agent": "MediaRouter-EmbyAdapter/0.9",
    })
    context = None if settings.get("verify_tls", True) else ssl._create_unverified_context()
    configured_timeout = max(float(settings["request_timeout_seconds"]), 0.1)
    deadline = time.monotonic() + configured_timeout
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            remaining = max(deadline - time.monotonic(), 0.1)
            with urlopen(request, timeout=remaining, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = exc
            break
        except (URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            remaining = deadline - time.monotonic()
            if attempt == 2 or remaining <= 0:
                break
            time.sleep(min(0.1 * (2 ** attempt), remaining))
        except Exception as exc:
            last_error = exc
            break
    state, message = _sanitize_error(last_error or RuntimeError("unknown Emby request failure"))
    raise EmbyError(message, state) from last_error


def test_emby_connection() -> EmbyConnectionResult:
    try:
        info = _request_json("/System/Info")
        if not isinstance(info, dict):
            raise EmbyError("Emby returned an unexpected response.", "error")
        return EmbyConnectionResult(success=True, health_state="healthy", message="Connected to Emby.",
            server_id=str(info.get("Id") or "") or None, server_name=str(info.get("ServerName") or "") or None,
            server_version=str(info.get("Version") or "") or None)
    except EmbyError as exc:
        return EmbyConnectionResult(success=False, health_state=exc.health_state, message=str(exc))


def _candidate_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"path", "url", "streamurl", "directstreamurl", "transcodingurl", "mediasourceid", "liveStreamId".lower(), "channelid"}:
                strings.extend(_candidate_strings(item))
            elif isinstance(item, (dict, list)):
                strings.extend(_candidate_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_candidate_strings(item))
    return strings


def _runtime_identity(raw: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    for value in _candidate_strings(raw):
        decoded = unquote(value)
        match = RUNTIME_RE.search(decoded)
        if match:
            route = match.group(1).lower()
            return match.group(2), ROUTE_MEDIA_TYPES[route], match.group(0)
        try:
            marker = parse_qs(urlsplit(decoded).query).get("mr_catalog_id", [None])[0]
        except ValueError:
            marker = None
        if marker and re.fullmatch(r"[A-Za-z0-9_.:-]+", marker):
            return marker, "channel", f"mr_catalog_id={marker}"
    return None, None, None


def _safe_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", text) else None


def _emby_identity_metadata(raw: dict[str, Any], item: dict[str, Any], play_state: dict[str, Any]) -> tuple[str | None, dict[str, str], list[str], list[str]]:
    current_program = raw.get("CurrentProgram") if isinstance(raw.get("CurrentProgram"), dict) else {}
    if not current_program and isinstance(item.get("CurrentProgram"), dict):
        current_program = item["CurrentProgram"]
    provider_ids: dict[str, str] = {}
    for owner in (item, current_program, raw):
        values = owner.get("ProviderIds") if isinstance(owner.get("ProviderIds"), dict) else {}
        for key, value in values.items():
            safe_key, safe_value = _safe_id(key), _safe_id(value)
            if safe_key and safe_value:
                provider_ids[safe_key] = safe_value
    tuner_ids: list[str] = []
    for owner in (play_state, item, current_program, raw):
        for key in ("MediaSourceId", "LiveStreamId", "TunerChannelId", "TunerId", "SourceId", "ChannelId"):
            value = _safe_id(owner.get(key))
            if value and value not in tuner_ids:
                tuner_ids.append(value)
    def collect_nested(value: Any) -> None:
        if isinstance(value, dict):
            nested_providers = value.get("ProviderIds") if isinstance(value.get("ProviderIds"), dict) else {}
            for key, nested_value in nested_providers.items():
                safe_key, safe_value = _safe_id(key), _safe_id(nested_value)
                if safe_key and safe_value:
                    provider_ids[safe_key] = safe_value
            for key, nested_value in value.items():
                if key.lower() in {"mediasourceid", "livestreamid", "tunerchannelid", "tunerid", "sourceid", "channelid"}:
                    safe_value = _safe_id(nested_value)
                    if safe_value and safe_value not in tuner_ids:
                        tuner_ids.append(safe_value)
                if isinstance(nested_value, (dict, list)):
                    collect_nested(nested_value)
        elif isinstance(value, list):
            for nested_value in value:
                collect_nested(nested_value)
    collect_nested(raw)
    media_sources = item.get("MediaSources") if isinstance(item.get("MediaSources"), list) else []
    for media_source in media_sources:
        if isinstance(media_source, dict):
            source_id = _safe_id(media_source.get("Id"))
            if source_id and source_id not in tuner_ids:
                tuner_ids.append(source_id)
    program_id = _safe_id(current_program.get("Id") or item.get("ProgramId") or raw.get("ProgramId"))
    candidates: list[str] = []
    for value in (*provider_ids.values(), *tuner_ids, program_id, _safe_id(item.get("Id"))):
        if value and value not in candidates:
            candidates.append(value)
    return program_id, provider_ids, tuner_ids, candidates


def normalize_emby_sessions(payload: Any, server_id: str | None = None, observed_at: datetime | None = None) -> list[EmbyPlaybackSession]:
    if not isinstance(payload, list):
        raise EmbyError("Emby returned an unexpected sessions response.", "error")
    observed_at = observed_at or _now()
    sessions: list[EmbyPlaybackSession] = []
    for raw in payload:
        if not isinstance(raw, dict) or not isinstance(raw.get("NowPlayingItem"), dict):
            continue
        item = raw["NowPlayingItem"]
        play_state = raw.get("PlayState") if isinstance(raw.get("PlayState"), dict) else {}
        catalog_id, media_type, runtime_path = _runtime_identity(raw)
        program_id, provider_ids, tuner_ids, identity_candidates = _emby_identity_metadata(raw, item, play_state)
        item_type = str(item.get("Type") or "") or None
        if media_type is None:
            media_type = {"movie": "movie", "episode": "episode", "tvchannel": "channel",
                          "channel": "channel", "livetvchannel": "channel"}.get((item_type or "").lower())
        session_id = str(raw.get("Id") or "").strip()
        if not session_id:
            continue
        sessions.append(EmbyPlaybackSession(
            emby_server_id=server_id, emby_session_id=session_id,
            emby_play_session_id=str(play_state.get("PlaySessionId") or raw.get("PlaySessionId") or "") or None,
            emby_device_id=str(raw.get("DeviceId") or "") or None,
            device_name=str(raw.get("DeviceName") or "") or None,
            client_name=str(raw.get("Client") or raw.get("ApplicationVersion") or "") or None,
            emby_user_id=str(raw.get("UserId") or "") or None,
            user_name=str(raw.get("UserName") or "") or None,
            emby_item_id=str(item.get("Id") or "") or None, item_name=str(item.get("Name") or "") or None,
            item_type=item_type,
            emby_media_source_id=str(play_state.get("MediaSourceId") or item.get("MediaSourceId") or "") or None,
            live_stream_id=str(play_state.get("LiveStreamId") or "") or None,
            emby_channel_id=str(item.get("ChannelId") or raw.get("ChannelId") or "") or None,
            emby_program_id=program_id, emby_provider_ids=provider_ids,
            emby_tuner_source_ids=tuner_ids, catalog_identity_candidates=identity_candidates,
            playback_position_ticks=int(play_state.get("PositionTicks") or 0),
            playback_state="paused" if bool(play_state.get("IsPaused")) else "playing",
            catalog_item_id=catalog_id, media_type=media_type, runtime_path=runtime_path,
            original_media_path_present=bool(item.get("Path")),
            direct_catalog_identity_available=bool(catalog_id),
            observed_at=observed_at,
        ))
    return sessions


def _enrich_unresolved_live_sessions(payload: Any, settings: dict[str, Any]) -> Any:
    """Fetch full Emby item DTOs when /Sessions omits durable live-channel metadata."""
    if not isinstance(payload, list):
        return payload
    enriched_ids: set[str] = set()
    for raw in payload:
        if not isinstance(raw, dict) or not isinstance(raw.get("NowPlayingItem"), dict):
            continue
        item = raw["NowPlayingItem"]
        item_type = str(item.get("Type") or "").lower()
        if item_type not in {"tvchannel", "channel", "livetvchannel"} or _runtime_identity(raw)[0]:
            continue
        item_id = _safe_id(item.get("Id"))
        user_id = _safe_id(raw.get("UserId"))
        if not item_id or not user_id or item_id in enriched_ids:
            continue
        path = (f"/Users/{quote(user_id, safe='')}/Items/{quote(item_id, safe='')}"
                "?Fields=Path,ProviderIds,MediaSources,MediaStreams,Overview")
        try:
            detail = _request_json(path, settings)
        except EmbyError as exc:
            add_log("warning", "emby", f"emby_item_identity_enrichment_failed item_id={item_id} error={str(exc)}")
            continue
        if not isinstance(detail, dict):
            add_log("warning", "emby", f"emby_item_identity_enrichment_failed item_id={item_id} error=unexpected_response")
            continue
        # Session playback state wins; the full item supplies omitted identity fields.
        merged = dict(detail)
        merged.update({key: value for key, value in item.items()
                       if key not in {"Path", "ProviderIds", "MediaSources"} and value not in (None, "", [], {})})
        for key in ("Path", "ProviderIds", "MediaSources"):
            if not merged.get(key) and item.get(key):
                merged[key] = item[key]
        raw["NowPlayingItem"] = merged
        enriched_ids.add(item_id)
        add_log("info", "emby", f"emby_item_identity_enriched item_id={item_id} path_present={bool(detail.get('Path'))} provider_ids={len(detail.get('ProviderIds') or {})} media_sources={len(detail.get('MediaSources') or [])}")
    return payload


def _binding_key(session: EmbyPlaybackSession) -> str:
    # The Emby session is the durable consumer lifecycle. PlaySessionId is playback
    # metadata and may rotate independently, so it must never own the binding.
    return f"{session.emby_server_id or 'unknown'}:{session.emby_session_id}"


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _binding_from_row(row: sqlite3.Row) -> EmbyPlaybackBinding:
    values = dict(row)
    for key in ("first_observed_at", "last_observed_at", "last_confirmed_playing_at", "missing_since", "released_at", "created_at", "updated_at"):
        values[key] = _dt(values.get(key))
    values.pop("binding_key", None)
    return EmbyPlaybackBinding(**values)


def list_emby_bindings(limit: int = 100, offset: int = 0) -> list[EmbyPlaybackBinding]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM emby_playback_bindings ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                            (min(max(limit, 1), 500), max(offset, 0))).fetchall()
    return [_binding_from_row(row) for row in rows]


def list_observed_sessions(limit: int = 100, offset: int = 0) -> list[EmbyPlaybackSession]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT payload_json FROM emby_observed_sessions ORDER BY observed_at DESC LIMIT ? OFFSET ?",
                            (min(max(limit, 1), 500), max(offset, 0))).fetchall()
    return [EmbyPlaybackSession(**json.loads(row["payload_json"])) for row in rows]


def _active_binding(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM emby_playback_bindings WHERE binding_key=? AND released_at IS NULL", (key,)).fetchone()


def list_emby_channel_mappings() -> list[EmbyChannelMapping]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM emby_channel_mappings ORDER BY catalog_item_id IS NULL DESC, emby_channel_name, emby_item_id").fetchall()
    return [EmbyChannelMapping(**dict(row)) for row in rows]


def page_emby_channel_mappings(limit: int = 100, offset: int = 0, search: str = "") -> EmbyChannelMappingPage:
    limit, offset = min(max(limit, 1), 200), max(offset, 0)
    term = f"%{search.strip()}%"
    where = "WHERE emby_channel_name LIKE ? OR emby_item_id LIKE ? OR COALESCE(catalog_item_id,'') LIKE ?" if search.strip() else ""
    args = (term, term, term) if where else ()
    with closing(_connect()) as conn:
        total = conn.execute(f"SELECT COUNT(*) count FROM emby_channel_mappings {where}", args).fetchone()["count"]
        rows = conn.execute(f"""SELECT * FROM emby_channel_mappings {where} ORDER BY
            CASE WHEN catalog_item_id IS NULL AND EXISTS (
                SELECT 1 FROM emby_observed_sessions observed
                WHERE json_extract(observed.payload_json,'$.emby_item_id')=emby_channel_mappings.emby_item_id
                  AND json_extract(observed.payload_json,'$.emby_server_id')=emby_channel_mappings.integration_id
                  AND COALESCE(json_extract(observed.payload_json,'$.binding_status'),'unmatched')='unmatched'
            ) THEN 0 WHEN catalog_item_id IS NULL THEN 1 ELSE 2 END,
            emby_channel_name,emby_item_id LIMIT ? OFFSET ?""",
                            (*args, limit, offset)).fetchall()
    return EmbyChannelMappingPage(total=total, limit=limit, offset=offset,
        items=[EmbyChannelMapping(**dict(row)) for row in rows])


def link_emby_channel(emby_server_id: str, emby_item_id: str, catalog_item_id: str,
                      emby_media_source_id: str | None = None) -> EmbyChannelMapping:
    now = _now().isoformat()
    with closing(_connect()) as conn:
        if not conn.execute("SELECT 1 FROM catalog_items WHERE internal_id=? AND media_type='channel'", (catalog_item_id,)).fetchone():
            raise ValueError("Live catalog item not found")
        conn.execute("""INSERT INTO emby_channel_mappings
            (emby_server_id,integration_id,emby_item_id,emby_media_source_id,emby_channel_name,catalog_item_id,mapping_source,created_at,updated_at)
            VALUES (?,?,?,?,NULL,?,'manual',?,?)
            ON CONFLICT(emby_server_id,emby_item_id) DO UPDATE SET
            integration_id=excluded.integration_id,
            emby_media_source_id=COALESCE(excluded.emby_media_source_id,emby_channel_mappings.emby_media_source_id),
            catalog_item_id=excluded.catalog_item_id,mapping_source='manual',updated_at=excluded.updated_at""",
            (emby_server_id, emby_server_id, emby_item_id, emby_media_source_id, catalog_item_id, now, now))
        conn.commit()
        row = conn.execute("SELECT * FROM emby_channel_mappings WHERE emby_server_id=? AND emby_item_id=?", (emby_server_id, emby_item_id)).fetchone()
    return EmbyChannelMapping(**dict(row))


def delete_emby_channel_mapping(integration_id: str, emby_item_id: str) -> bool:
    with closing(_connect()) as conn:
        deleted = conn.execute("DELETE FROM emby_channel_mappings WHERE integration_id=? AND emby_item_id=?",
                               (integration_id, emby_item_id)).rowcount
        conn.commit()
    return bool(deleted)


def _catalog_from_identity_values(conn: sqlite3.Connection, values: set[str]) -> str | None:
    expanded = {value[3:] if value.lower().startswith("mr:") else value for value in values if value}
    if not expanded:
        return None
    placeholders = ",".join("?" for _ in expanded)
    args = tuple(expanded)
    rows = conn.execute(f"""SELECT DISTINCT items.internal_id FROM catalog_items items
        LEFT JOIN source_availability sources ON sources.catalog_internal_id=items.internal_id
        WHERE items.media_type='channel' AND (items.internal_id IN ({placeholders})
          OR items.cuid IN ({placeholders}) OR items.tvg_id IN ({placeholders})
          OR sources.external_id IN ({placeholders}))""", args * 4).fetchall()
    return rows[0]["internal_id"] if len(rows) == 1 else None


def preview_emby_channel_mappings() -> EmbyChannelMappingPreview:
    settings = _private_settings()
    info = _request_json("/System/Info", settings)
    server_id = str(info.get("Id") or "unknown") if isinstance(info, dict) else "unknown"
    payload = _request_json("/LiveTv/Channels?Fields=Path,ProviderIds,MediaSources,Tags,CurrentProgram", settings)
    channels = payload.get("Items", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    preview: list[EmbyChannelMappingPreviewItem] = []
    with closing(_connect()) as conn:
        emby_name_counts: dict[str, int] = {}
        for item in channels:
            if isinstance(item, dict):
                name = re.sub(r"\s+", " ", str(item.get("Name") or "").strip().lower())
                emby_name_counts[name] = emby_name_counts.get(name, 0) + 1
        for item in channels:
            if not isinstance(item, dict) or not _safe_id(item.get("Id")):
                continue
            raw = {"NowPlayingItem": item, "PlayState": {}}
            runtime_catalog_id, _, _ = _runtime_identity(raw)
            _, providers, sources, candidates = _emby_identity_metadata(raw, item, {})
            tags = item.get("Tags") if isinstance(item.get("Tags"), list) else []
            marker_values = {value for value in (*providers.values(), *tags) if str(value).lower().startswith("mr:")}
            if runtime_catalog_id:
                marker_values.add(runtime_catalog_id)
            marker_catalog = _catalog_from_identity_values(conn, {str(value) for value in marker_values})
            media_source_id = next((value for value in sources if value != item.get("ChannelId")), None)
            previous = conn.execute("SELECT catalog_item_id,mapping_source,created_at FROM emby_channel_mappings WHERE emby_server_id=? AND emby_item_id=?",
                                    (server_id, str(item["Id"]))).fetchone()
            if previous and previous["mapping_source"] == "manual":
                status, source, catalog_item_id, detail = "manual", "manual", previous["catalog_item_id"], "Preserved manual mapping."
                if marker_catalog and marker_catalog != catalog_item_id:
                    status, detail = "conflict", "Manual mapping conflicts with imported marker; manual mapping will be preserved."
            elif marker_values and not marker_catalog:
                status, source, catalog_item_id, detail = "conflict", "automatic_marker", None, "Marker did not resolve uniquely."
            elif marker_catalog:
                status, source, catalog_item_id, detail = "automatic", "automatic_marker", marker_catalog, "Exact durable marker match."
            elif previous and previous["catalog_item_id"]:
                status, source, catalog_item_id, detail = "automatic", previous["mapping_source"], previous["catalog_item_id"], "Preserved exact persisted ItemId mapping."
            else:
                normalized = re.sub(r"\s+", " ", str(item.get("Name") or "").strip().lower())
                catalog_rows = conn.execute("SELECT internal_id FROM catalog_items WHERE media_type='channel' AND normalized_title=?", (normalized,)).fetchall()
                if normalized and emby_name_counts.get(normalized) == 1 and len(catalog_rows) == 1:
                    status, source, catalog_item_id, detail = "automatic", "automatic_title", catalog_rows[0]["internal_id"], "Exact unique normalized title match."
                elif normalized and (emby_name_counts.get(normalized, 0) > 1 or len(catalog_rows) > 1):
                    status, source, catalog_item_id, detail = "ambiguous", "title", None, "Duplicate normalized channel name."
                else:
                    status, source, catalog_item_id, detail = "unmatched", "unmapped", None, "No safe exact match."
            preview.append(EmbyChannelMappingPreviewItem(integration_id=server_id,
                emby_item_id=str(item["Id"]), emby_media_source_id=media_source_id,
                emby_channel_name=str(item.get("Name") or "")[:256], catalog_item_id=catalog_item_id,
                match_source=source, status=status, detail=detail))
    return EmbyChannelMappingPreview(integration_id=server_id,
        automatic_matches=sum(item.status == "automatic" for item in preview),
        manual_matches=sum(item.status == "manual" for item in preview),
        ambiguous=sum(item.status == "ambiguous" for item in preview),
        unmatched=sum(item.status == "unmatched" for item in preview),
        conflicts=sum(item.status == "conflict" for item in preview), items=preview)


def refresh_emby_channel_mappings() -> EmbyChannelRefreshResult:
    plan = preview_emby_channel_mappings()
    now = _now().isoformat()
    mapped = 0
    with closing(_connect()) as conn:
        for item in plan.items:
            previous = conn.execute("SELECT mapping_source,created_at,catalog_item_id FROM emby_channel_mappings WHERE integration_id=? AND emby_item_id=?",
                                    (plan.integration_id, item.emby_item_id)).fetchone()
            if previous and previous["mapping_source"] == "manual":
                mapped += bool(previous["catalog_item_id"])
                continue
            safe = item.status == "automatic"
            conn.execute("""INSERT INTO emby_channel_mappings
                (emby_server_id,integration_id,emby_item_id,emby_media_source_id,emby_channel_name,catalog_item_id,mapping_source,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(emby_server_id,emby_item_id) DO UPDATE SET
                integration_id=excluded.integration_id,
                emby_media_source_id=excluded.emby_media_source_id,emby_channel_name=excluded.emby_channel_name,
                catalog_item_id=excluded.catalog_item_id,mapping_source=excluded.mapping_source,updated_at=excluded.updated_at""",
                (plan.integration_id,plan.integration_id,item.emby_item_id,item.emby_media_source_id,item.emby_channel_name,
                 item.catalog_item_id if safe else None,item.match_source if safe else 'unmapped',
                 previous["created_at"] if previous else now,now))
            mapped += safe
        conn.commit()
    add_log("info", "emby", f"emby_channel_mappings_refreshed server={plan.integration_id} discovered={len(plan.items)} mapped={mapped} unmapped={len(plan.items)-mapped}")
    return EmbyChannelRefreshResult(discovered=len(plan.items), mapped=mapped, unmapped=len(plan.items)-mapped)


def _resolve_durable_catalog_identity(conn: sqlite3.Connection, session: EmbyPlaybackSession) -> bool:
    """Resolve exact durable IDs only; titles are deliberately excluded."""
    mapping = conn.execute("""SELECT catalog_item_id,mapping_source FROM emby_channel_mappings
        WHERE integration_id=? AND catalog_item_id IS NOT NULL AND
        (emby_item_id=? OR (? IS NOT NULL AND emby_media_source_id=?))
        ORDER BY CASE
          WHEN mapping_source='manual' THEN 0
          WHEN mapping_source='automatic_marker' THEN 1
          WHEN emby_item_id=? THEN 2
          ELSE 3 END LIMIT 1""",
        (session.emby_server_id or "unknown", session.emby_item_id,
         session.emby_media_source_id, session.emby_media_source_id, session.emby_item_id)).fetchone()
    if mapping:
        session.catalog_item_id = mapping["catalog_item_id"]
        session.media_type = "channel"
        session.direct_catalog_identity_available = True
        session.correlation_method = f"emby_channel_mapping_{mapping['mapping_source']}"
        return True
    if session.catalog_item_id:
        row = conn.execute("SELECT internal_id,media_type FROM catalog_items WHERE internal_id=?",
                           (session.catalog_item_id,)).fetchone()
        if row and (not session.media_type or row["media_type"] == session.media_type):
            session.catalog_item_id = row["internal_id"]
            session.media_type = row["media_type"]
            session.direct_catalog_identity_available = True
            return True
        session.catalog_item_id = None
    durable_ids = {value for value in (session.catalog_identity_candidates or
        [session.emby_channel_id, session.emby_item_id, session.emby_program_id]) if value}
    if not durable_ids:
        return False
    placeholders = ",".join("?" for _ in durable_ids)
    values = tuple(durable_ids)
    rows = conn.execute(f"""SELECT DISTINCT items.internal_id,items.media_type
        FROM catalog_items items LEFT JOIN source_availability sources
          ON sources.catalog_internal_id=items.internal_id
        WHERE items.internal_id IN ({placeholders}) OR items.cuid IN ({placeholders})
           OR items.tvg_id IN ({placeholders}) OR sources.external_id IN ({placeholders})""",
        values * 4).fetchall()
    compatible = [row for row in rows if not session.media_type or row["media_type"] == session.media_type]
    if len(compatible) != 1:
        return False
    session.catalog_item_id = compatible[0]["internal_id"]
    session.media_type = compatible[0]["media_type"]
    session.direct_catalog_identity_available = True
    return True


def _correlate(conn: sqlite3.Connection, session: EmbyPlaybackSession) -> tuple[sqlite3.Row | None, str, str, dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "candidate_count": 0, "unmatched_reason": None,
        "recent_runtime_observation_found": False,
        "rejected_for_age_count": 0, "rejected_for_media_type_count": 0,
        "rejected_for_lifecycle_count": 0, "rejected_for_client_context_count": 0,
    }
    key = _binding_key(session)
    if session.reservation_id:
        exact = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id=? AND lifecycle_state IN ('provisional','active')",
                             (session.reservation_id,)).fetchone()
        if exact:
            return exact, session.correlation_method or "emby_acquired_reservation", "authoritative", diagnostics
    existing = _active_binding(conn, key)
    if existing and (not session.catalog_item_id or existing["catalog_item_id"] == session.catalog_item_id):
        reservation = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id=? AND lifecycle_state IN ('provisional','active')",
                                   (existing["reservation_id"],)).fetchone()
        if reservation:
            return reservation, "existing_binding", "authoritative", diagnostics
    direct_rows: list[sqlite3.Row] = []
    if session.catalog_item_id and session.media_type:
        direct_rows = conn.execute("""SELECT * FROM broker_reservations
            WHERE catalog_item_id=? AND media_type=? AND lifecycle_state IN ('provisional','active')
            ORDER BY CASE lifecycle_state WHEN 'provisional' THEN 0 ELSE 1 END, created_at DESC""",
            (session.catalog_item_id, session.media_type)).fetchall()
        if len(direct_rows) == 1:
            return direct_rows[0], "direct_runtime_catalog_identity", "authoritative", diagnostics
    rows = direct_rows
    from app.services.broker import _identity_hash
    stable_hash = _identity_hash(session.emby_device_id, "stable_client_id")
    session_hashes = {_identity_hash(value, "explicit_session") for value in
                      (session.emby_play_session_id, session.emby_session_id) if value}
    strong = [row for row in rows if (stable_hash and row["stable_client_id"] == stable_hash)
              or (row["client_session"] and row["client_session"] in session_hashes)]
    if len(strong) == 1:
        return strong[0], "catalog_and_emby_identity", "authoritative", diagnostics
    if len(strong) > 1:
        diagnostics["candidate_count"] = len(strong)
        diagnostics["unmatched_reason"] = "ambiguous_identity_candidates"
        return None, "ambiguous_identity_candidates", "none", diagnostics

    now = _now()
    cutoff = now - timedelta(seconds=EMBY_CORRELATION_WINDOW_SECONDS)
    all_observations = conn.execute("""SELECT observations.*, reservations.lifecycle_state,
            reservations.created_at AS reservation_created_at
        FROM runtime_correlation_observations observations
        JOIN broker_reservations reservations ON reservations.reservation_id=observations.reservation_id
        WHERE observations.expires_at>? ORDER BY observations.observed_at DESC""", (now.isoformat(),)).fetchall()
    diagnostics["rejected_for_age_count"] = sum(1 for row in all_observations
        if datetime.fromisoformat(row["observed_at"]) < cutoff)
    diagnostics["rejected_for_media_type_count"] = sum(1 for row in all_observations
        if session.media_type and row["media_type"] != session.media_type)
    diagnostics["rejected_for_lifecycle_count"] = sum(1 for row in all_observations
        if row["lifecycle_state"] not in CONSUMING_STATES)
    credible: list[sqlite3.Row] = []
    for row in all_observations:
        if datetime.fromisoformat(row["observed_at"]) < cutoff:
            continue
        if session.media_type and row["media_type"] != session.media_type:
            continue
        if session.catalog_item_id and row["catalog_item_id"] != session.catalog_item_id:
            continue
        if row["lifecycle_state"] not in CONSUMING_STATES:
            continue
        if not session.catalog_item_id and row["lifecycle_state"] != "provisional":
            continue
        stable_match = bool(stable_hash and row["stable_emby_identifier_hash"] == stable_hash)
        emby_profile = str(row["request_profile"] or "").startswith("emby_")
        if not (stable_match or emby_profile):
            diagnostics["rejected_for_client_context_count"] += 1
            continue
        credible.append(row)
    by_reservation: dict[str, sqlite3.Row] = {}
    for row in credible:
        by_reservation.setdefault(row["reservation_id"], row)
    candidates = list(by_reservation.values())
    diagnostics["recent_runtime_observation_found"] = bool(credible)
    diagnostics["candidate_count"] = len(candidates)
    if len(candidates) == 1:
        reservation = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id=?",
                                   (candidates[0]["reservation_id"],)).fetchone()
        method = "emby_recent_runtime_request_catalog" if session.catalog_item_id else "emby_recent_runtime_request_unique"
        return reservation, method, "high", diagnostics
    if len(candidates) > 1:
        diagnostics["unmatched_reason"] = "ambiguous_recent_runtime_observations"
        return None, "ambiguous_recent_runtime_observations", "none", diagnostics
    within_window = [row for row in all_observations
                     if datetime.fromisoformat(row["observed_at"]) >= cutoff]
    media_compatible = [row for row in within_window
                        if not session.media_type or row["media_type"] == session.media_type]
    lifecycle_compatible = [row for row in media_compatible if row["lifecycle_state"] in CONSUMING_STATES]
    if not session.media_type:
        reason = "media_type_unavailable"
    elif not all_observations:
        reason = "no_recent_runtime_observations"
    elif not within_window:
        reason = "runtime_observations_outside_window"
    elif not media_compatible:
        reason = "runtime_observation_media_type_mismatch"
    elif not lifecycle_compatible:
        reason = "runtime_observation_reservation_not_consuming"
    elif diagnostics["rejected_for_client_context_count"]:
        reason = "runtime_observation_client_context_incompatible"
    else:
        reason = "no_compatible_emby_runtime_observation"
    diagnostics["unmatched_reason"] = reason
    return None, reason, "none", diagnostics


def _acquire_emby_reservation(session: EmbyPlaybackSession) -> None:
    """Ensure an authoritative active Emby observation has a broker candidate."""
    with closing(_connect()) as conn:
        durable = _resolve_durable_catalog_identity(conn, session)
        if not durable:
            session.unmatched_reason = "catalog_identity_unresolved"
            diagnostic = {
                "item_id": session.emby_item_id, "channel_id": session.emby_channel_id,
                "program_id": session.emby_program_id, "media_source_id": session.emby_media_source_id,
                "provider_ids": session.emby_provider_ids, "tuner_source_ids": session.emby_tuner_source_ids,
                "runtime_marker": bool(session.runtime_path), "path_present": session.original_media_path_present,
                "item_type": session.item_type, "name": re.sub(r"\s+", " ", session.item_name or "")[:128],
            }
            add_log("warning", "emby", f"emby_catalog_identity_unresolved session={session.emby_session_id} identity_fields={json.dumps(diagnostic, separators=(',', ':'), sort_keys=True)}")
            return
        add_log("info", "emby", f"emby_catalog_identity_resolved session={session.emby_session_id} catalog_item={session.catalog_item_id} media_type={session.media_type} method={session.correlation_method or 'durable_metadata'} runtime_marker={bool(session.runtime_path)}")
        existing = _active_binding(conn, _binding_key(session))
        if existing and existing["catalog_item_id"] == session.catalog_item_id:
            reservation = conn.execute("SELECT * FROM broker_reservations WHERE reservation_id=? AND lifecycle_state IN ('provisional','active')",
                                       (existing["reservation_id"],)).fetchone()
            if reservation:
                session.reservation_id = reservation["reservation_id"]
                session.correlation_method = "existing_binding"
                return
    try:
        decision = resolve_source(
            catalog_item_id=session.catalog_item_id,
            media_type=session.media_type,
            client_session=session.emby_session_id,
            client_label="Emby observed playback",
            allow_reservation_reuse=True,
            lifecycle_enabled=True,
            meaningful_activity=True,
        )
    except BrokerUnavailable as exc:
        session.unmatched_reason = exc.detail.failure_code
        return
    if not decision.reservation:
        session.unmatched_reason = "broker_reservation_unavailable"
        return
    session.reservation_id = decision.reservation.reservation_id
    if not (session.correlation_method and session.correlation_method.startswith("emby_channel_mapping_")):
        session.correlation_method = "emby_explicit_session_reused" if decision.reservation_reused else "emby_explicit_session_created"
    event = "emby_reservation_reused" if decision.reservation_reused else "emby_reservation_created"
    add_log("info", "emby", f"{event} session={session.emby_session_id} reservation={decision.reservation.reservation_id} catalog_item={session.catalog_item_id} account={decision.reservation.account_name or decision.reservation.account_id}")


def _release_binding(conn: sqlite3.Connection, row: sqlite3.Row, reason: str, now: datetime) -> None:
    conn.execute("UPDATE emby_playback_bindings SET released_at=?,release_reason=?,updated_at=? WHERE id=? AND released_at IS NULL",
                 (now.isoformat(), reason, now.isoformat(), row["id"]))


def _upsert_observed(conn: sqlite3.Connection, sessions: list[EmbyPlaybackSession]) -> None:
    conn.execute("DELETE FROM emby_observed_sessions")
    for session in sessions:
        conn.execute("INSERT INTO emby_observed_sessions(binding_key,payload_json,observed_at) VALUES (?,?,?)",
                     (_binding_key(session), session.model_dump_json(), session.observed_at.isoformat()))


def reconcile_emby_sessions(sessions: list[EmbyPlaybackSession], *, server_id: str, release_grace_seconds: int) -> tuple[int, int]:
    now = _now()
    matched = unmatched = 0
    for session in sessions:
        session.emby_server_id = server_id
        _acquire_emby_reservation(session)
    seen_keys = {_binding_key(session) for session in sessions}
    lifecycle_actions: list[tuple[str, str, str]] = []
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for session in sessions:
            key = _binding_key(session)
            old = _active_binding(conn, key)
            if old:
                bound_state = conn.execute("SELECT lifecycle_state FROM broker_reservations WHERE reservation_id=?",
                                           (old["reservation_id"],)).fetchone()
                if not bound_state or bound_state["lifecycle_state"] in TERMINAL_STATES:
                    _release_binding(conn, old, "bound_reservation_terminal", now)
                    old = None
            identity_changed = bool(old and (
                (session.catalog_item_id and old["catalog_item_id"] != session.catalog_item_id)
                or (session.direct_catalog_identity_available and session.emby_item_id and old["emby_item_id"] != session.emby_item_id)
                or (session.direct_catalog_identity_available and session.emby_media_source_id and old["emby_media_source_id"] != session.emby_media_source_id)
            ))
            if identity_changed:
                old_catalog = old["catalog_item_id"]
                _release_binding(conn, old, "emby_item_changed", now)
                lifecycle_actions.append(("release", old["reservation_id"], "emby_playback_stopped"))
                old = None
                add_log("info", "emby", f"emby_channel_switched session={session.emby_session_id} old_catalog={old_catalog} new_catalog={session.catalog_item_id} item_id={session.emby_item_id or 'none'} media_source_id={session.emby_media_source_id or 'none'}")
            if session.unmatched_reason in {"catalog_identity_unresolved", "no_sources", "all_disabled", "all_unhealthy", "all_at_capacity", "broker_reservation_unavailable"}:
                reservation, method, confidence = None, session.unmatched_reason, "none"
                diagnostics = {"recent_runtime_observation_found": False, "candidate_count": 0,
                    "unmatched_reason": session.unmatched_reason, "rejected_for_age_count": 0,
                    "rejected_for_media_type_count": 0, "rejected_for_lifecycle_count": 0,
                    "rejected_for_client_context_count": 0}
            else:
                reservation, method, confidence, diagnostics = _correlate(conn, session)
                if session.correlation_method and session.correlation_method.startswith(("emby_recent_runtime_request", "emby_channel_mapping_")):
                    method = session.correlation_method
            session.recent_runtime_observation_found = session.recent_runtime_observation_found or diagnostics["recent_runtime_observation_found"]
            session.correlation_candidate_count = max(session.correlation_candidate_count, diagnostics["candidate_count"])
            session.unmatched_reason = diagnostics["unmatched_reason"]
            session.rejected_for_age_count = diagnostics["rejected_for_age_count"]
            session.rejected_for_media_type_count = diagnostics["rejected_for_media_type_count"]
            session.rejected_for_lifecycle_count = diagnostics["rejected_for_lifecycle_count"]
            session.rejected_for_client_context_count = diagnostics["rejected_for_client_context_count"]
            if reservation is None:
                unmatched += 1
                session.correlation_method = method
                session.correlation_confidence = confidence
                session.binding_status = "unmatched"
                add_log("warning", "emby", (
                    f"playback_not_correlated session={session.emby_session_id} "
                    f"catalog_identity={session.direct_catalog_identity_available} media_type={session.media_type or 'unknown'} "
                    f"reason={session.unmatched_reason} candidates={session.correlation_candidate_count} "
                    f"recent_runtime_observation={session.recent_runtime_observation_found} "
                    f"rejected_age={session.rejected_for_age_count} "
                    f"rejected_media_type={session.rejected_for_media_type_count} "
                    f"rejected_lifecycle={session.rejected_for_lifecycle_count} "
                    f"rejected_client_context={session.rejected_for_client_context_count}"
                ))
                continue
            matched += 1
            session.reservation_id = reservation["reservation_id"]
            session.catalog_item_id = reservation["catalog_item_id"]
            session.media_type = reservation["media_type"]
            session.correlation_method = method
            session.correlation_confidence = confidence
            session.binding_status = "bound"
            current = _active_binding(conn, key)
            if current:
                conn.execute("""UPDATE emby_playback_bindings SET playback_state=?,last_observed_at=?,
                    last_confirmed_playing_at=?,missing_since=NULL,updated_at=?,device_name=?,client_name=?,
                    user_name=?,item_name=? WHERE id=?""", (session.playback_state, now.isoformat(),
                    now.isoformat(), now.isoformat(), session.device_name, session.client_name,
                    session.user_name, session.item_name, current["id"]))
                if current["missing_since"]:
                    add_log("info", "emby", f"missing_playback_restored binding={current['id']} reservation={reservation['reservation_id']}")
                add_log("info", "emby", f"emby_binding_refreshed binding={current['id']} reservation={reservation['reservation_id']} session={session.emby_session_id}")
            else:
                binding_id = uuid4().hex
                conn.execute("""INSERT INTO emby_playback_bindings
                    (id,binding_key,emby_server_id,emby_session_id,emby_play_session_id,emby_device_id,
                     emby_user_id,emby_item_id,emby_media_source_id,reservation_id,catalog_item_id,media_type,
                     playback_state,first_observed_at,last_observed_at,last_confirmed_playing_at,
                     correlation_method,correlation_confidence,device_name,client_name,user_name,item_name,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (binding_id,key,server_id,session.emby_session_id,session.emby_play_session_id,
                     session.emby_device_id,session.emby_user_id,session.emby_item_id,session.emby_media_source_id,
                     reservation["reservation_id"],reservation["catalog_item_id"],reservation["media_type"],
                     session.playback_state,now.isoformat(),now.isoformat(),now.isoformat(),method,confidence,
                     session.device_name,session.client_name,session.user_name,session.item_name,now.isoformat(),now.isoformat()))
                add_log("info", "emby", f"emby_binding_created binding={binding_id} reservation={reservation['reservation_id']} session={session.emby_session_id} catalog_item={reservation['catalog_item_id']}")
            lifecycle_actions.append(("confirm" if reservation["lifecycle_state"] == "provisional" else "heartbeat",
                                      reservation["reservation_id"], "emby_playback_confirmed"))
            add_log("info", "emby", f"playback_correlated session={session.emby_session_id} reservation={reservation['reservation_id']} catalog_item={reservation['catalog_item_id']} method={method} confidence={confidence}")
        active = conn.execute("SELECT * FROM emby_playback_bindings WHERE released_at IS NULL AND emby_server_id=?", (server_id,)).fetchall()
        for binding in active:
            if binding["binding_key"] in seen_keys:
                continue
            if not binding["missing_since"]:
                conn.execute("UPDATE emby_playback_bindings SET missing_since=?,updated_at=? WHERE id=?",
                             (now.isoformat(), now.isoformat(), binding["id"]))
                add_log("info", "emby", f"emby_release_grace_started binding={binding['id']} reservation={binding['reservation_id']} grace_seconds={release_grace_seconds}")
                continue
            if now - datetime.fromisoformat(binding["missing_since"]) < timedelta(seconds=release_grace_seconds):
                continue
            other = conn.execute("""SELECT 1 FROM emby_playback_bindings WHERE reservation_id=?
                AND released_at IS NULL AND missing_since IS NULL AND id != ? LIMIT 1""",
                (binding["reservation_id"], binding["id"])).fetchone()
            if other:
                continue
            _release_binding(conn, binding, "emby_session_disappeared", now)
            lifecycle_actions.append(("release", binding["reservation_id"], "emby_session_disappeared"))
        _upsert_observed(conn, sessions)
        conn.commit()
    for action, reservation_id, reason in lifecycle_actions:
        if action == "confirm":
            confirm_reservation(reservation_id, source=reason)
            add_log("info", "emby", f"emby_reservation_promoted reservation={reservation_id} reason={reason}")
        elif action == "heartbeat":
            heartbeat_reservation(reservation_id, source="emby_playback_heartbeat")
        else:
            release_reservation(reservation_id, reason=reason)
            add_log("info", "emby", f"emby_reservation_released reservation={reservation_id} reason={reason}")
    return matched, unmatched


def record_poll_failure(error: EmbyError) -> None:
    current = _status_store().read()
    failures = int(current.get("consecutive_failures") or 0) + 1
    now = _now()
    first_failure = _dt(current.get("first_failure_at")) or now
    timeout = int(_private_settings().get("unavailable_timeout_seconds") or 60)
    health = error.health_state
    if health == "degraded" and now - first_failure >= timedelta(seconds=timeout):
        health = "error"
    _status_store().update({"health_state": health, "last_poll_attempt": now.isoformat(),
        "consecutive_failures": failures, "last_error": str(error), "first_failure_at": first_failure.isoformat()})
    if failures == 1:
        add_log("warning", "emby", f"connection_lost health={error.health_state} error={str(error)}")


def _restart_pending_grace_after_outage(now: datetime) -> None:
    with closing(_connect()) as conn:
        conn.execute("""UPDATE emby_playback_bindings SET missing_since=?,updated_at=?
            WHERE released_at IS NULL AND missing_since IS NOT NULL""", (now.isoformat(), now.isoformat()))
        conn.commit()


def poll_emby_once() -> EmbyIntegrationStatus:
    settings = _private_settings()
    if not settings.get("enabled"):
        _status_store().update({"health_state": "disabled", "last_error": None})
        return get_emby_status()
    if not settings.get("server_url") or not settings.get("api_key"):
        record_poll_failure(EmbyError("Emby integration is not fully configured.", "not_configured"))
        return get_emby_status()
    attempt = _now()
    add_log("info", "emby", "poll_started")
    try:
        info = _request_json("/System/Info", settings)
        payload = _request_json("/Sessions", settings)
        payload = _enrich_unresolved_live_sessions(payload, settings)
        if not isinstance(info, dict):
            raise EmbyError("Emby returned an unexpected server response.", "error")
        server_id = str(info.get("Id") or "unknown")
        sessions = normalize_emby_sessions(payload, server_id, attempt)
        previous = _status_store().read()
        restored = int(previous.get("consecutive_failures") or 0) > 0
        if restored:
            _restart_pending_grace_after_outage(attempt)
        matched, unmatched = reconcile_emby_sessions(sessions, server_id=server_id,
            release_grace_seconds=int(settings["release_grace_seconds"]))
        _status_store().update({"health_state": "healthy", "last_poll_attempt": attempt.isoformat(),
            "last_successful_poll": _now().isoformat(), "consecutive_failures": 0,
            "server_id": server_id, "server_name": str(info.get("ServerName") or "") or None,
            "server_version": str(info.get("Version") or "") or None,
            "observed_playback_count": len(sessions), "matched_playback_count": matched,
            "unmatched_playback_count": unmatched, "last_error": None})
        _status_store().update({"first_failure_at": None})
        if restored:
            add_log("info", "emby", "connection_restored")
        add_log("info", "emby", f"poll_completed observed={len(sessions)} matched={matched} unmatched={unmatched}")
    except EmbyError as exc:
        record_poll_failure(exc)
    return get_emby_status()


def get_emby_status() -> EmbyIntegrationStatus:
    settings = get_emby_settings()
    state = _status_store().read()
    with closing(_connect()) as conn:
        active = conn.execute("SELECT COUNT(*) count FROM emby_playback_bindings WHERE released_at IS NULL").fetchone()["count"]
        pending = conn.execute("SELECT COUNT(*) count FROM emby_playback_bindings WHERE released_at IS NULL AND missing_since IS NOT NULL").fetchone()["count"]
    return EmbyIntegrationStatus(enabled=settings.enabled,
        configured=bool(settings.server_url and settings.has_api_key),
        health_state="disabled" if not settings.enabled else state.get("health_state", "unknown"),
        last_poll_attempt=_dt(state.get("last_poll_attempt")),
        last_successful_poll=_dt(state.get("last_successful_poll")),
        consecutive_failures=int(state.get("consecutive_failures") or 0),
        server_id=state.get("server_id"), server_name=state.get("server_name"),
        server_version=state.get("server_version"),
        observed_playback_count=int(state.get("observed_playback_count") or 0),
        matched_playback_count=int(state.get("matched_playback_count") or 0),
        unmatched_playback_count=int(state.get("unmatched_playback_count") or 0),
        active_binding_count=active, pending_release_count=pending, last_error=state.get("last_error"))
