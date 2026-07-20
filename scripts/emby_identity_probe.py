#!/usr/bin/env python3
"""Capture the identity fields Emby exposes for imported channels and playback.

This diagnostic is deliberately read-only. It uses the configured MediaRouter Emby
connection, performs GET requests only, and never calls Broker or binding lifecycle
operations.
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

SENSITIVE_KEYS = {
    "access_token", "accesstoken", "api_key", "apikey", "authorization",
    "password", "token", "x-emby-token",
}
IDENTITY_KEYS = {
    "Id", "Name", "Type", "Path", "ChannelId", "ProgramId",
    "MediaSourceId", "LiveStreamId", "TunerChannelId", "TunerId", "SourceId",
    "ProviderIds", "Tags", "MediaSources", "CurrentProgram", "PlayState",
    "NowPlayingItem", "DeviceId", "DeviceName", "Client", "UserId", "UserName",
    "PlaySessionId",
}
MEDIA_SOURCE_KEYS = {
    "Id", "Name", "Path", "Protocol", "Type", "Container", "IsRemote",
    "SupportsDirectPlay", "SupportsDirectStream", "SupportsTranscoding",
    "LiveStreamId", "TunerChannelId", "ProviderIds",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                result[key] = "<redacted>"
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _media_source_projection(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {key: _redact(item[key]) for key in MEDIA_SOURCE_KEYS if key in item}
        for item in value if isinstance(item, dict)
    ]


def _identity_projection(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in IDENTITY_KEYS:
        if key not in item:
            continue
        if key == "MediaSources":
            result[key] = _media_source_projection(item[key])
        elif key == "NowPlayingItem" and isinstance(item[key], dict):
            result[key] = _identity_projection(item[key])
        elif key == "CurrentProgram" and isinstance(item[key], dict):
            result[key] = _identity_projection(item[key])
        elif key == "PlayState" and isinstance(item[key], dict):
            result[key] = {
                child_key: _redact(child_value)
                for child_key, child_value in item[key].items()
                if child_key in IDENTITY_KEYS or child_key in {"PositionTicks", "IsPaused"}
            }
        else:
            result[key] = _redact(item[key])
    catalog_id, media_type, marker = _runtime_identity(item)
    result["media_router_identity_detected"] = {
        "catalog_item_id": catalog_id,
        "media_type": media_type,
        "marker": marker,
    }
    return result


def capture(channel_id: str | None = None) -> dict[str, Any]:
    settings = _private_settings()
    info = _request_json("/System/Info", settings)
    channel_payload = _request_json(
        "/LiveTv/Channels?Fields=Path,ProviderIds,MediaSources,Tags,CurrentProgram",
        settings,
    )
    channels = channel_payload.get("Items", []) if isinstance(channel_payload, dict) else channel_payload
    if not isinstance(channels, list):
        raise EmbyError("Emby returned an unexpected channels response.", "error")

    selected_channels = [item for item in channels if isinstance(item, dict)]
    if channel_id:
        selected_channels = [item for item in selected_channels if str(item.get("Id") or "") == channel_id]
        if not selected_channels:
            raise EmbyError(f"Emby channel {channel_id!r} was not found.", "error")

    sessions_payload = _request_json("/Sessions", settings)
    enriched_payload = _enrich_unresolved_live_sessions(sessions_payload, settings)
    normalized = normalize_emby_sessions(
        enriched_payload,
        server_id=str(info.get("Id") or "unknown") if isinstance(info, dict) else "unknown",
    )

    projected_sessions: list[dict[str, Any]] = []
    raw_sessions = enriched_payload if isinstance(enriched_payload, list) else []
    for raw in raw_sessions:
        if not isinstance(raw, dict) or not isinstance(raw.get("NowPlayingItem"), dict):
            continue
        if channel_id and str(raw["NowPlayingItem"].get("Id") or "") != channel_id:
            continue
        projected_sessions.append(_identity_projection(raw))

    normalized_sessions = [
        session.model_dump(mode="json") for session in normalized
        if not channel_id or session.emby_item_id == channel_id
    ]

    return {
        "probe_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "server": {
            "id": str(info.get("Id") or "") if isinstance(info, dict) else None,
            "name": str(info.get("ServerName") or "") if isinstance(info, dict) else None,
            "version": str(info.get("Version") or "") if isinstance(info, dict) else None,
        },
        "requested_channel_id": channel_id,
        "channels": [_identity_projection(item) for item in selected_channels],
        "sessions": projected_sessions,
        "normalized_sessions": normalized_sessions,
        "interpretation": {
            "channel_count": len(selected_channels),
            "active_session_count": len(projected_sessions),
            "direct_channel_markers": sum(
                bool(_runtime_identity({"NowPlayingItem": item})[0]) for item in selected_channels
            ),
            "direct_session_markers": sum(
                bool(session.get("media_router_identity_detected", {}).get("catalog_item_id"))
                for session in projected_sessions
            ),
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
