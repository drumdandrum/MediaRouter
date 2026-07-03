# Media Router Vision

## Product Intent

Media Router is a local, Dockerized, web-based orchestration platform for a home media ecosystem.

It is not intended to be only an IPTV broker. The long-term goal is for Media Router to become the single operational control plane for IPTV accounts, playlist imports, stream routing, account failover, active stream tracking, STRM generation, guide data, and media server integrations.

## Target Environment

Media Router is designed for an always-on Ubuntu Linux Docker server, colocated with home media services such as Emby, Jellyfin, NextPVR, and Channels DVR.

The application should assume that users may have existing tools and folders already in place. It should inspect, ask, and confirm instead of guessing.

## Core Product Goals

- Provide a web UI for normal configuration and operation.
- Avoid requiring users to edit YAML for everyday use.
- Maintain one internal catalog for movies, series, and live channels.
- Assign permanent internal IDs to catalog items.
- Route playback through stable Media Router URLs.
- Select IPTV accounts based on priority, stream capacity, health, and failures.
- Fail over automatically when a provider account fails.
- Generate outputs such as STRM, M3U, XMLTV, and HDHomeRun-compatible endpoints from the same catalog.
- Integrate with Emby, Jellyfin, NextPVR, Channels DVR, IPTV Boss, and future services through adapters.

## Non-Goals For The Foundation Phase

- Do not implement a full IPTV broker yet.
- Do not implement account credential storage yet.
- Do not import or rewrite real STRM libraries yet.
- Do not expose HDHomeRun endpoints yet.
- Do not add service-specific integration logic yet.

This phase exists to define the architecture and project foundation before feature work begins.

## User Experience Principles

- The first-run wizard should guide setup and explain what was discovered.
- Destructive operations should require explicit confirmation.
- Existing folder structures should be preserved.
- Credentials should never be shown casually or logged.
- Diagnostics should be useful without exposing secrets.
- Advanced users should be able to inspect configuration, but the UI remains the primary control surface.
