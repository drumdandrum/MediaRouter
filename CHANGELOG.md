# Changelog

Media Router is a provider-agnostic media routing platform.

All notable changes to Media Router will be documented in this file.

---
# Unreleased

## Planned

- Provider adapters
- XMLTV generation
- Local media providers
- HDHomeRun emulation
- Additional runtime improvements

## Added

- Editorial Live channel placements that preserve every source-playlist group, channel number, display title, metadata set, and original position while retaining one canonical channel identity and runtime URL.
- Placement APIs and Catalog Live-channel placement detail views.

- v0.8.1 configurable STRM generation modes (Test, Small, Medium, Unlimited, and Custom), persistent movie/episode limits, and bounded batch sizes.
- Profiled and optimized STRM generation with batch timing diagnostics, tracking prefetch/bulk upserts, cached directory creation, atomic concurrent file writes, configurable bounded workers, and throughput metrics.
- Hardened runtime reservation acquisition for slow provider startup with an immediate SQLite transaction, an active-playback uniqueness key, race-winner reuse, and explicitly configured trusted proxy client headers.
- Added privacy-safe runtime fingerprint diagnostics, stable Emby client identifiers, reservation identity aliases, and a configurable 90-second origin-only startup fallback that safely coalesces generic ffmpeg-to-playback User-Agent transitions when exactly one candidate exists.
- Batch-level STRM progress, incremental tracking commits, cooperative cancellation, capped/unlimited dry-run reporting, catalog estimates, and paginated generated-file history.
- Matching Live M3U Test, Small, Medium, Unlimited, and Custom channel limits; eligibility estimates; explicit Unlimited confirmation; bounded catalog reads; and streamed, ordered playlist generation.

- STRM output settings for movie and series output directories.
- STRM dry-run preview for create, update, skip, and orphan cleanup actions.
- STRM generation job for movie and episode catalog items.
- Generated STRM file tracking in SQLite.
- Outputs UI page with settings, runtime base preview, run summary, and generated-file history.
- STRM output path validation for output directories, `/data`, and configured IPTVBoss import path.
- Live TV M3U output settings, dry-run preview, generation job, validation, history, and preview API.
- Live TV M3U channel-number preservation with `tvg-chno` import, output, and channel-number sorting.

## Fixed

- Runtime playback correlation now ignores ephemeral source ports, normalizes insignificant User-Agent version changes, and reuses matching active reservations for their full lifetime.
- Serialized SQLite reuse-or-create prevents concurrent Emby/ffmpeg probes, seeks, and reconnects from consuming multiple Broker account slots.

- Live M3U generation now emits one entry per active editorial placement, restoring intentional repeated CUID/channel memberships across IPTV Boss groups without duplicating catalog identities.
- Re-import now upserts placement positions and marks removed source-playlist positions stale instead of multiplying placement rows.

- Replaced the hard-coded 500 movie/500 episode materialization with paginated, memory-stable catalog processing while retaining 500/500 defaults for existing installations.
- Replaced the implicit 500-channel Live M3U cap with persistent safe presets while retaining a 500-channel Test default for existing settings.

- Hardened STRM Generate path handling so missing child directories are created under configured output roots.
- Improved STRM Generate failures with path-specific job messages and Logs page diagnostics.
- Added Docker/application logging for STRM dry-run, generate, validation, counts, and exceptions.
- Runtime playback reservations now default to four-hour TTLs while manual Broker tests keep the short testing TTL.
- Outputs page ordering now keeps recent generated files at the bottom and warns about overlapping output paths.
- Added `HEAD` support for runtime redirect URLs so Jellyfin/media-server probes receive `302` instead of `405`.
- Repeated runtime `GET` requests from the same short-lived client/session now reuse the active reservation instead of consuming extra account capacity.
- Broker page now auto-refreshes reservation/account status with polling while visible.

## Notes

- Generated STRM files contain Media Router runtime URLs only.
- Generated Live TV M3U playlists contain Media Router runtime URLs only.
- Guide XML is currently external and served separately from IPTV Boss/webserver output.
- Broker reservations currently release by TTL expiration; client heartbeat and explicit playback-end release are future work.
- Runtime redirect mode may work in clients such as VLC, but some media servers may eventually require a future proxy mode.
- Broker live status uses polling for now; WebSockets or Server-Sent Events remain future options.
- Provider URLs and credentials are not written to generated STRM files.
- XMLTV, HDHomeRun output, media-server sync, proxy streaming, and transcoding remain deferred.

---

# v0.6.0 – Runtime Source Resolution

**Release Date:** July 2026

## Added

- Broker duplicate-reservation diagnostics and an explicit maintenance action that releases redundant active reservations only when catalog, media type, and hashed playback identity match.

- Runtime URL endpoints for:
  - `/r/live/{catalog_id}`
  - `/r/movie/{catalog_id}`
  - `/r/episode/{catalog_id}`
- HTTP redirect-based runtime resolution.
- Debug JSON resolve mode.
- Reservation-aware runtime routing.
- Runtime URL previews.
- Runtime preview API.
- Client labels.
- Adjustable reservation TTL.

## Improved

- Broker integration with runtime resolver.
- Reservation diagnostics.
- Runtime debugging.
- Credential masking in debug output.

## Notes

This release establishes the Media Router Runtime API.

Clients no longer need direct knowledge of provider URLs. All future integrations should consume Media Router runtime URLs instead.

---

# v0.5.1 – Broker Polish & Explainability

## Added

- Broker decision explanations.
- Candidate evaluation display.
- Reservation diagnostics.
- Broker status improvements.
- Structured broker responses.

## Fixed

- Fixed `[object Object]` rendering.
- Improved Broker UI feedback.
- Improved reservation refresh behavior.

---

# v0.5.0 – Broker Decision Engine

## Added

- Broker decision engine.
- Reservation system.
- Capacity-aware account selection.
- Priority groups.
- Weight-based routing.
- Reservation expiration.
- Broker API.
- Broker dashboard.

## Notes

This release introduced the core routing engine that determines which account should satisfy a media request.

---

# v0.4.0 – Providers & Accounts

## Added

- Provider management.
- Account management.
- Account priorities.
- Account weights.
- Maximum simultaneous stream limits.
- Provider persistence.
- Account persistence.

## Notes

Media Router now supports multiple providers with multiple accounts per provider.

---

# v0.3.0 – Catalog Engine

## Added

- Catalog database.
- Stable internal media IDs.
- M3U import.
- Live channel parsing.
- Movie parsing.
- Series parsing.
- Episode parsing.
- Source mappings.
- Catalog UI.
- Catalog APIs.

## Notes

The Catalog became the authoritative identity layer for all media.

---

# v0.2.1 – Foundation Polish

## Added

- Persistent settings.
- Improved setup wizard.
- About page improvements.
- Dashboard enhancements.
- Docker metadata.
- Version metadata.

## Fixed

- Settings persistence.
- Docker volume persistence.
- Foundation UI polish.

---

# v0.1.0 – Foundation

## Added

- Initial FastAPI application.
- Docker deployment.
- SQLite persistence.
- Dashboard.
- Settings.
- Jobs.
- Logs.
- Setup wizard.
- Documentation framework.

## Notes

Initial project foundation.
