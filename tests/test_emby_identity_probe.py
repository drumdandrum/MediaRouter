from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "emby_identity_probe.py"
spec = importlib.util.spec_from_file_location("emby_identity_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(probe)


def test_identity_projection_preserves_marker_and_excludes_secret_keys():
    projected = probe._identity_projection({
        "Id": "15747",
        "Path": "http://router/r/live/channel-123",
        "ProviderIds": {"TvgId": "mr:channel-123"},
        "MediaSources": [{"Id": "source-1", "Path": "http://router/r/live/channel-123", "Token": "secret"}],
        "ApiKey": "secret",
    })
    assert "ApiKey" not in projected
    assert "Token" not in projected["MediaSources"][0]
    assert projected["media_router_identity_detected"]["catalog_item_id"] == "channel-123"


def test_capture_reads_channel_and_session_surfaces(monkeypatch):
    calls: list[str] = []

    def fake_request(path, settings):
        calls.append(path)
        if path == "/System/Info":
            return {"Id": "server-1", "ServerName": "Emby", "Version": "4.x"}
        if path.startswith("/LiveTv/Channels"):
            return {"Items": [{"Id": "15747", "Name": "Probe", "Type": "TvChannel", "ProviderIds": {}, "MediaSources": [{"Id": "source-1"}]}]}
        if path == "/Sessions":
            return [{"Id": "session-1", "UserId": "user-1", "NowPlayingItem": {"Id": "15747", "Name": "Probe", "Type": "TvChannel"}, "PlayState": {"MediaSourceId": "source-1", "PlaySessionId": "play-1"}}]
        raise AssertionError(path)

    monkeypatch.setattr(probe, "_private_settings", lambda: {"server_url": "http://emby", "api_key": "hidden"})
    monkeypatch.setattr(probe, "_request_json", fake_request)
    monkeypatch.setattr(probe, "_enrich_unresolved_live_sessions", lambda payload, settings: payload)

    payload = probe.capture("15747")
    assert calls == [
        "/System/Info",
        "/LiveTv/Channels?Fields=Path,ProviderIds,MediaSources,Tags,CurrentProgram",
        "/Sessions",
    ]
    assert payload["read_only"] is True
    assert payload["interpretation"]["active_session_count"] == 1
    assert payload["interpretation"]["direct_session_markers"] == 0
    assert payload["normalized_sessions"][0]["catalog_item_id"] is None
