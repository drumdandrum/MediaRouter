# Changelog

Media Router is a provider-agnostic media routing platform.

All notable changes are documented here.

---

# Unreleased

## Planned

- Native HTTP-served Live M3U.
- XMLTV strategy and HTTP-served XMLTV.
- Provider/account health monitoring.
- Configurable reservation policies.
- Backup, restore, migration, and upgrade hardening.
- Dashboard catalog-count clarification.
- Setup wizard refinements.

## Known considerations

- Large STRM libraries should preferably be generated on local storage when Media Router and the media server share a host. Network shares can become metadata and round-trip bottlenecks for hundreds of thousands of tiny files.
- Kodi IPTV Simple may apply client-specific channel ordering or duplicate-placement behavior even with the original IPTV Boss playlist.

---

# v0.8.1 — Production Readiness and Reservation Coalescing

**Release Date:** July 2026

## Added

- Configurable STRM generation modes: Test, Small, Medium, Unlimited, and Custom.
- Persistent movie and episode limits with bounded batch sizes.
- Batch-level STRM progress, incremental tracking commits, cooperative cancellation, and benchmark metrics.
- Bounded concurrent atomic STRM writes, bulk tracking updates, and cached directory creation.
- Configurable Live M3U Test, Small, Medium, Unlimited, and Custom limits.
- Live M3U eligibility estimates, explicit Unlimited confirmation, paginated selection, and streamed ordered output.
- Editorial Live channel placements that preserve source-playlist groups, channel numbers, display titles, metadata, and original order while retaining one canonical channel identity.
- Placement APIs and Catalog placement-detail views.
- Privacy-safe runtime fingerprint diagnostics.
- Trusted-proxy-aware client identity configuration.
- Reservation identity aliases and a configurable short startup-coalescing fallback for Emby/ffmpeg User-Agent transitions.

## Improved

- STRM generation throughput and memory behavior for large catalogs.
- Broker reservation correlation across GET, HEAD, Range, seek, reconnect, and slow-start requests.
- Live TV M3U metadata and repeated-group placement preservation.
- Outputs page ordering and generated-file history pagination.
- Runtime reservation defaults for playback versus manual diagnostic tests.

## Fixed

- Ephemeral source ports no longer alter playback identity.
- Insignificant User-Agent version changes no longer create additional reservations.
- SQLite reservation acquisition is atomic and reuses the race-winning row.
- One Emby playback no longer consumes multiple accounts merely because probe and playback requests use different request agents.
- Repeated Live channel placements are emitted without duplicating canonical catalog identity.
- Re-import updates placement positions and deactivates removed placements instead of multiplying rows.
- Hard-coded 500-item materialization was replaced with safe configurable presets and explicit Unlimited confirmation.
- Missing child output directories can be created beneath valid configured roots.
- STRM generation failures now include path-specific diagnostics.
- Runtime `HEAD` requests return redirect headers instead of `405`.
- Broker status updates automatically while the page is visible.

## Validation

- Emby Live TV and STRM movie playback validated.
- Channels DVR Live TV ingestion and playback validated.
- Jellyfin runtime STRM playback exercised.
- VLC runtime playback exercised.

## Notes

- Generated STRM files contain Media Router runtime URLs only.
- Generated Live TV M3U playlists contain Media Router runtime URLs only.
- Guide XML remains external for now.
- Reservations currently release by TTL expiration.
- Media Router resolves and redirects playback; it does not currently proxy or transcode streams.
- Native HTTP output endpoints, XMLTV, HDHomeRun emulation, and formal media-server adapters remain future work.

---

# v0.8.0 — Live TV Output

## Added

- Live TV M3U output settings, validation, dry-run, generation, history, and preview.
- Stable `/r/live/{catalog_item_id}` runtime URLs in generated playlists.
- Live channel number and group metadata preservation.

---

# v0.7.0 — STRM Generation

## Added

- Movie and episode STRM output generation.
- Runtime-URL-only STRM content.
- Output path validation, dry-run, generated-file tracking, and cleanup support.

---

# v0.6.0 — Runtime Source Resolution

## Added

- Runtime URL endpoints for live channels, movies, and episodes.
- HTTP redirect-based runtime resolution.
- Debug JSON resolve mode.
- Reservation-aware runtime routing.
- Runtime URL previews and adjustable TTL.

## Notes

This release established the Runtime API so clients no longer require direct knowledge of provider URLs.

---

# v0.5.1 — Broker Polish and Explainability

## Added

- Broker decision explanations.
- Candidate evaluation display.
- Reservation diagnostics and status improvements.

---

# v0.5.0 — Broker Decision Engine

## Added

- Capacity-aware Broker decision engine.
- Reservation system.
- Priority groups and weight-based routing.
- Reservation expiration and Broker UI.

---

# v0.4.0 — Providers and Accounts

## Added

- Provider and account management.
- Priorities, weights, stream limits, and persistence.

---

# v0.3.0 — Catalog Engine

## Added

- Catalog database and stable media IDs.
- M3U import for live channels, movies, series, and episodes.
- Source mappings, Catalog UI, and Catalog APIs.

---

# v0.2.1 — Foundation Polish

## Added

- Persistent settings and wizard improvements.
- About page, dashboard, Docker metadata, and version metadata.

---

# v0.1.0 — Foundation

## Added

- Initial FastAPI and Docker application.
- SQLite persistence.
- Dashboard, settings, jobs, logs, setup wizard, and documentation framework.
