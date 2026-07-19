# Backlog

This backlog is organized by product milestone. Completed implementation history remains available in Git tags and the changelog.

## Core Platform — Complete ✅

- [x] FastAPI and Docker foundation.
- [x] Persistent settings, setup wizard, jobs, logs, and About/System metadata.
- [x] SQLite catalog engine and stable internal IDs.
- [x] M3U import for live channels, movies, series, and episodes.
- [x] Provider and account management.
- [x] Source availability and multi-account deduplication.
- [x] Capacity-aware Broker with priorities, weights, reservations, expiry, and release.
- [x] Broker explanations, candidate diagnostics, live polling, and Release All Active.
- [x] Stable runtime URLs and HTTP redirects.
- [x] GET, HEAD, Range, reconnect, and slow-start reservation reuse.
- [x] Atomic SQLite reservation reuse-or-create.
- [x] Trusted-proxy-aware client identity.
- [x] Emby startup identity coalescing and aliases.
- [x] Provisional and active capacity leases with evidence-based promotion.
- [x] Sliding active renewal and explicit lifecycle APIs.
- [x] Atomic same-identity Live switching and conservative provisional VOD supersession.
- [x] MediaRouter-side Emby session polling, correlation, health, bindings, and lifecycle evidence.
- [x] STRM generation for movies and episodes.
- [x] STRM path validation, dry-run, tracking, cleanup, cancellation, and history.
- [x] Configurable STRM presets and custom limits.
- [x] Batched, paginated, memory-stable STRM generation.
- [x] Bounded concurrent atomic STRM writes and benchmark logging.
- [x] Live TV M3U generation.
- [x] Live M3U path validation, dry-run, preview, history, and configurable limits.
- [x] Channel-number, group, metadata, source-order, and repeated-placement preservation.
- [x] Emby Live TV and STRM movie playback validation.
- [x] Channels DVR Live TV ingestion and playback validation.
- [x] Jellyfin runtime STRM playback exercise.
- [x] VLC runtime playback exercise.

## Production Readiness — Current 🚧

### Dashboard and clarity

- [ ] Fix or relabel the dashboard Catalog card so unique catalog items are not confused with source-availability rows.
- [ ] Show unique channels, movies, series, episodes, total catalog items, and source rows as distinct metrics.
- [ ] Review status labels for production clarity.

### Operations

- [ ] Backup and restore guidance/tooling.
- [ ] Database migration tests.
- [ ] Upgrade notes and rollback procedure.
- [ ] Health diagnostics page or report.
- [ ] Review structured logging and secret scrubbing coverage.
- [ ] Optional local-network UI authentication.
- [ ] Document production and development environment separation.
- [ ] Document local STRM output as the preferred same-host deployment model.

### Provider health

- [ ] Define provider/account health scoring.
- [ ] Add periodic provider/account health checks.
- [ ] Integrate health into Broker eligibility and explanations.
- [ ] Add health history and last-check visibility.

### Reservation policy

- [x] Make runtime TTL configurable by media type.
- [x] Make startup-coalescing window configurable and visible.
- [x] Document explicit session, fingerprint, and alias behavior.
- [x] Add provisional TTL, promotion, sliding renewal, and supersession policy.
- [ ] Define stale-reservation cleanup policy.
- [x] Add client-agnostic confirm, heartbeat, and release endpoints.
- [x] Connect Emby playback observation to lifecycle endpoints through polling.
- [ ] Add stronger explicit Emby stop events only if a future plugin/webhook phase is approved.
- [ ] Connect Jellyfin/Kodi events to lifecycle endpoints.
- [ ] Evaluate optional proxy byte/disconnect observation.

### Catalog and output polish

- [ ] Add configurable title normalization rules for provider/language/quality prefixes.
- [ ] Improve movie and series filename normalization.
- [ ] Validate large local STRM generation and scan performance.
- [ ] Add clear warnings for network-backed STRM output paths where detectable.

## Core v1.0

### Native HTTP outputs

- [ ] Serve generated Live M3U directly from Media Router over HTTP.
- [ ] Establish XMLTV ingestion/generation strategy.
- [ ] Serve XMLTV directly from Media Router over HTTP.
- [ ] Provide stable client-facing output URLs.
- [ ] Retire the temporary static file server from the recommended deployment.

### Setup and release

- [ ] Refine initial setup wizard for providers, accounts, imports, runtime URL, and outputs.
- [ ] Add backup/restore acceptance tests.
- [ ] Add migration and upgrade acceptance tests.
- [ ] Publish supported Docker deployment guidance.
- [ ] Publish Core v1.0 release notes.

## Post-1.0 Ecosystem

- [ ] Runtime proxy mode for clients that cannot reliably consume redirects.
- [ ] HDHomeRun emulation.
- [ ] Emby adapter.
- [ ] Jellyfin adapter.
- [ ] Channels DVR enhancements.
- [ ] IPTV Boss import watcher.
- [ ] Local media providers.
- [ ] Cloud or remote-storage providers.
- [ ] Existing STRM scanner/importer.
- [ ] Output plugin registry and formal plugin SDK.
- [ ] WebSockets or Server-Sent Events for Broker updates.
- [ ] Kodi-specific M3U compatibility profile.

## Known client behavior

- Emby and Channels DVR consume the Live M3U output successfully.
- Kodi IPTV Simple can play the output but may apply its own channel order or duplicate-placement behavior.
- The same Kodi behavior occurs with the original IPTV Boss playlist, so it is not currently treated as a Media Router core-output defect.
