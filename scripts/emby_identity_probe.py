#!/usr/bin/env python3
"""Capture identity fields Emby exposes for imported channels and playback.

This diagnostic performs Emby GET requests only. It does not call Broker or binding
lifecycle operations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.services.emby import (
    EmbyError,
    _enrich_unresolved_live_sessions,
    _private_settings,
    _request_json,
    _runtime_identity,
    normalize_emby_sessions,
)

SENSITIVE_KEYS = {"access_token", "accesstoken", "api_key", "apikey", "authorization", "password", "token", "x-emby-token"}
IDENTITY_KEYS = {"Id", "Name", "Type", "Path", "ChannelId", "ProgramId", "MediaSourceId", "LiveStreamId", "TunerChannelId", "TunerId", "SourceId", "ProviderIds", "Tags", "MediaSources", "CurrentProgram", "PlayState", "NowPlayingItem", "DeviceId", "DeviceName", "Client", "UserId", "UserName", "PlaySessionId"}
MEDIA_SOURCE_KEYS = {"Id", "Name", "Path", "Protocol", "Type", "Container", "IsRemote", "SupportsDirectPlay", "SupportsDirectStream", "SupportsTranscoding", "LiveStreamId", "TunerChannelId", "ProviderIds"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "<redacted>" if key.lower() in SENSITIVE_KEYS else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _identity_projection(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in IDENTITY_KEYS:
        if key not in item:
            continue
        value = item[key]
        if key == "MediaSources" and isinstance(value, list):
            result[key] = [{child: _redact(source[child]) for child in MEDIA_SOURCE_KEYS if child in source} for source in value if isinstance(source, dict)]
        elif key in {"NowPlayingItem", "CurrentProgram"} and isinstance(value, dict):
            result[key] = _identity_projection(value)
        elif key == "PlayState" and isinstance(value, dict):
            result[key] = {child: _redact(child_value) for child, child_value in value.items() if child in IDENTITY_KEYS or child in {"PositionTicks", "IsPaused"}}
        else:
            result[key] = _redact(value)
    catalog_id, media_type, marker = _runtime_identity(item)
    result["media_router_identity_detected"] = {"catalog_item_id": catalog_id, "media_type": media_type, "marker": marker}
    return result


def capture(channel_id: str | None = None) -> dict[str, Any]:
    settings = _private_settings()
    info = _request_json("/System/Info", settings)
    channel_payload = _request_json("/LiveTv/Channels?Fields=Path,ProviderIds,MediaSources,Tags,CurrentProgram", settings)
    channels = channel_payload.get("Items", []) if isinstance(channel_payload, dict) else channel_payload
    if not isinstance(channels, list):
        raise EmbyError("Emby returned an unexpected channels response.", "error")
    selected = [item for item in channels if isinstance(item, dict) and (not channel_id or str(item.get("Id") or "") == channel_id)]
    if channel_id and not selected:
        raise EmbyError(f"Emby channel {channel_id!r} was not found.", "error")

    sessions_payload = _request_json("/Sessions", settings)
    enriched = _enrich_unresolved_live_sessions(sessions_payload, settings)
    server_id = str(info.get("Id") or "unknown") if isinstance(info, dict) else "unknown"
    normalized = normalize_emby_sessions(enriched, server_id=server_id)
    raw_sessions = enriched if isinstance(enriched, list) else []
    sessions = [
        _identity_projection(raw) for raw in raw_sessions
        if isinstance(raw, dict) and isinstance(raw.get("NowPlayingItem"), dict)
        and (not channel_id or str(raw["NowPlayingItem"].get("Id") or "") == channel_id)
    ]
    normalized_sessions = [session.model_dump(mode="json") for session in normalized if not channel_id or session.emby_item_id == channel_id]
    return {
        "probe_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "server": {"id": server_id, "name": str(info.get("ServerName") or "") if isinstance(info, dict) else None, "version": str(info.get("Version") or "") if isinstance(info, dict) else None},
        "requested_channel_id": channel_id,
        "channels": [_identity_projection(item) for item in selected],
        "sessions": sessions,
        "normalized_sessions": normalized_sessions,
        "interpretation": {
            "channel_count": len(selected),
            "active_session_count": len(sessions),
            "direct_channel_markers": sum(bool(_runtime_identity({"NowPlayingItem": item})[0]) for item in selected),
            "direct_session_markers": sum(bool(session.get("media_router_identity_detected", {}).get("catalog_item_id")) for session in sessions),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-id", help="Limit capture to one Emby channel ItemId.")
    parser.add_argument("--output", type=Path, default=Path("emby-identity-probe.json"))
    args = parser.parse_args()
    try:
        payload = capture(args.channel_id)
    except EmbyError as exc:
        parser.error(str(exc))
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote read-only Emby identity capture to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
