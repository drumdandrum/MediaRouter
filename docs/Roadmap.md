# Roadmap

Media Router planning now uses product milestones rather than implementation sprints.

## Milestone 1 — Core Platform ✅

Status: Complete and validated in real client workflows.

Delivered:

- FastAPI and Docker foundation.
- Persistent settings, wizard, jobs, logs, and system metadata.
- SQLite catalog identity for channels, movies, series, and episodes.
- Provider/account model and source availability.
- Capacity-aware Broker with priority, weight, reservations, expiry, release, diagnostics, and polling UI.
- Stable runtime URLs for live channels, movies, and episodes.
- HTTP redirect-based source resolution.
- Atomic reservation reuse for probes, seeks, reconnects, and slow startup.
- Conservative Emby startup fingerprint coalescing and aliases.
- Provisional-to-active reservation leases, sliding renewal, and explicit lifecycle APIs.
- Atomic same-session Live switching and provisional VOD supersession.
- Movie and episode STRM generation.
- Live TV M3U generation.
- Editorial channel-placement preservation.
- Configurable output limits and paginated generation.
- Bounded STRM filesystem concurrency and benchmark logging.

Validated clients:

- Emby Live TV and STRM movie playback.
- Channels DVR Live TV ingestion and playback.
- Jellyfin runtime STRM playback.
- VLC runtime playback.
- Kodi IPTV Simple playback, with client-specific ordering limitations.

## Milestone 2 — Production Readiness 🚧

Status: Current focus.

Goals:

- Run continuously on the home media server with predictable upgrades and recovery.
- Improve operational clarity without expanding scope into a full media server.

Planned work:

- Correct dashboard labeling for unique catalog items versus source-availability rows.
- Provider/account health checks and health-scoring policy.
- Operational validation of configurable reservation lifecycle policies.
- Backup and restore guidance or tooling.
- Database migration tests and upgrade notes.
- Runtime URL configuration polish.
- Setup wizard refinements.
- UI authentication option for deployments that require it.
- Structured diagnostics and secret-scrubbing review.
- Local-output deployment guidance for large STRM libraries.

Exit criteria:

- Clean install, upgrade, restart, and restore procedures are documented and tested.
- Dashboard counts and status labels are unambiguous.
- Provider health can affect Broker decisions safely.
- Reservation policies are configurable and documented.
- The app can run continuously on the production host without manual repair.

## Milestone 3 — Media Router Core v1.0

Goals:

- Complete the core output and distribution surface while preserving module boundaries.

Planned work:

- Native HTTP-served Live M3U endpoint.
- XMLTV ingestion/generation strategy.
- Native HTTP-served XMLTV endpoint.
- Stable output URLs that remove the need for the temporary port-8090 file server.
- Initial setup flow for providers, accounts, catalog imports, runtime URL, and outputs.
- Backup/restore and migration acceptance tests.
- Release documentation and supported deployment model.

Exit criteria:

- Clients can consume Live M3U and XMLTV directly from Media Router over HTTP.
- Emby and Channels DVR can be configured without a separate static file server.
- A new installation can be configured from the UI with documented defaults.
- Core state can be backed up, restored, and upgraded safely.

## Milestone 4 — Media Ecosystem v1.x

Potential work:

- HDHomeRun emulation.
- Runtime proxy mode for clients that cannot consume redirects reliably.
- Emby/Jellyfin/Kodi playback-event integration with lifecycle APIs.
- Optional proxy-mode byte/disconnect observation.
- Emby and Jellyfin adapters.
- Channels DVR enhancements.
- IPTV Boss folder watcher.
- Local media providers.
- Cloud or remote-storage providers.
- Kodi-specific output compatibility profile.
- WebSockets or Server-Sent Events if Broker polling becomes insufficient.

These are valuable ecosystem features but are not prerequisites for Core v1.0.

## Architectural boundaries

Media Router owns:

- Catalog identity.
- Source availability.
- Provider/account capacity.
- Broker decisions.
- Stable runtime URLs.
- Disposable output generation and distribution.

Client applications own:

- Playback UI.
- DVR and recording behavior.
- Viewing history.
- Transcoding and media presentation.
- Client-specific guide and library presentation.

Media Router does not aim to become another Emby, Jellyfin, NextPVR, or Channels DVR server.
