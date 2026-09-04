# Backlog

This backlog reflects the implementation at `v0.10.0` (`88da1bc`). Completed history
also remains in release notes and Git. Proposed work is not described as implemented.

## Delivered through v0.10.0

- [x] Provider/account management and provider-specific source availability.
- [x] Canonical catalog IDs with separate source rows and editorial live placements.
- [x] M3U import for channels, movies, series, and episodes.
- [x] Atomic capacity-aware Broker with provisional/active leases, renewal, expiry,
  release, supersession, explanations, and operator controls.
- [x] Stable runtime URLs and VOD redirects with conservative identity reuse.
- [x] Reservation-aware live gateway with provider URL containment, Range support,
  bounded failover, heartbeat, and disconnect/EOF release.
- [x] STRM movie/episode generation, scoped builds, path safety, bounded concurrency,
  tracking/history, cleanup, cancellation, and benchmarks.
- [x] Live M3U generation, placement preservation, preview/dry-run, bounds, and history.
- [x] Emby polling, status, normalized observations, durable bindings, correlation,
  lifecycle evidence, reservation adoption, and failure-safe release grace.
- [x] Automatic/manual live channel mappings and exact manual VOD mapping CRUD.
- [x] Read-only bounded Emby mapping audit with production operating limits.
- [x] Dashboard catalog/source and visible/authoritative capacity clarity.
- [x] Shared diagnostic redaction and secret-safe public runtime behavior.
- [x] Versioned transactional startup migrations and request-schema boundary tests.
- [x] Consistency-safe backup, validation, empty-target restore, and acceptance tests.
- [x] Production/development separation, local STRM storage, upgrade, and rollback
  guidance.
- [x] Channels DVR generic Live M3U ingestion and gateway playback validation.

## v0.11.0 — Native Live TV Distribution

### Guide contract and identity

- [ ] Record an accepted XMLTV ownership decision: IPTV Boss remains editorial source;
  Media Router validates and publishes a disposable derived guide artifact.
- [ ] Define supported XMLTV encodings, namespaces, timestamps, maximum sizes/counts,
  malformed-input behavior, and freshness policy.
- [ ] Define deterministic `tvg-id` joins and diagnostics for duplicates, missing guide
  channels, guide-only channels, and repeated playlist placements.
- [ ] Decide whether v0.11.0 preserves complete source XMLTV or emits a bounded filtered
  representation; document the choice before implementation.

### Output publication boundary

- [x] Extract canonical Live M3U representation from the existing combined output
  service without changing disk generation semantics.
- [ ] Publish M3U/XMLTV atomically and retain the last known-good pair after a failed or
  cancelled rebuild.
- [ ] Record bounded build history, content digest, source freshness, counts, warnings,
  and active artifact metadata.
- [x] Ensure native reads generate from catalog state and disk regeneration remains
  atomic, so neither path exposes a partial artifact.

### Native HTTP delivery

- [x] Serve current authoritative Live M3U state at `/live/playlist.m3u`.
- [ ] Serve the current XMLTV artifact at one stable Media Router URL.
- [x] Implement UTF-8 M3U content type, GET, stable ETag, bounded error contracts, and
  provider/path/secret redaction. HEAD/conditional 304 support remains follow-up.
- [x] Do not accept arbitrary filesystem paths through delivery routes.
- [x] Preserve filesystem output as the v0.10.0 compatibility and rollback path.

### Operator workflow

- [ ] Add XMLTV source/output configuration and non-mutating validation/preview.
- [ ] Show stable M3U/XMLTV URLs, last successful build, artifact freshness, join counts,
  warnings, and sanitized failures in the UI/API.
- [ ] Provide explicit manual regeneration with progress/cancellation consistent with
  existing output jobs.
- [ ] Update installation guidance to remove the temporary static server from the
  recommended Channels topology only after validation.

### Verification and release

- [ ] Add deterministic parser/join/publication/API tests with sanitized fixtures.
- [ ] Add large-guide memory/time bounds and malformed/hostile XML tests.
- [ ] Test atomic failure, cancellation, concurrent delivery, restart, migration, and
  backup/restore behavior.
- [ ] Validate M3U and XMLTV ingestion on an isolated Channels instance.
- [ ] Validate channel/guide identity, Range playback, capacity refusal, failover,
  heartbeat, disconnect release, regeneration, and restart through Channels.
- [ ] Document opt-in production validation and configuration-only rollback.
- [ ] Publish v0.11.0 release notes only after all completion criteria pass.

## Near-term structural and operational work

- [ ] Move Broker expiry housekeeping out of write-capable GET/list paths into an
  explicit maintenance boundary without changing expiration semantics.
- [ ] Add a consolidated sanitized health report for database, migrations, Broker,
  outputs, jobs, and integration polling.
- [ ] Define interrupted background-job reporting and restart behavior before adding
  unattended scheduled imports or builds.
- [ ] Define provider/account health scoring, checks, eligibility effects, explanations,
  and bounded history.
- [ ] Define a stale-reservation cleanup policy from production evidence.
- [ ] Add optional local-network UI authentication.
- [ ] Refine setup for providers, accounts, imports, runtime URL, and outputs.

## Catalog and Emby follow-up

- [ ] Keep the VOD source-entry ledger shadow-only until identity evidence and migration
  rules justify authoritative reconciliation.
- [ ] Define configurable title/filename normalization without mutating editorial input.
- [ ] Add resumable/cursor-based movie audit beyond the 10,000-item per-run bound.
- [ ] Replace full episode-detail audit retention with bounded streaming aggregation.
- [ ] Add a deterministic audit hash helper with complete stable ordering.
- [ ] Design durable movie, series, and episode crosswalks separately from the read-only
  audit; never bulk-apply ambiguous title matches.
- [ ] Design media-type-aware abandoned-session handling after live, movie, and episode
  polling evidence is broad enough to tune it safely.
- [ ] Consider a sanitized downloadable audit report after the API workflow stabilizes.

## Deferred ecosystem work

- [ ] VOD proxy mode and byte/disconnect observation.
- [ ] Jellyfin and Kodi playback lifecycle adapters.
- [ ] Optional stronger Emby stop events through an approved plugin/webhook design.
- [ ] HDHomeRun emulation and tuner discovery.
- [ ] Channels-specific enhancements beyond standards-based M3U/XMLTV consumption.
- [ ] IPTV Boss export watcher and unattended regeneration.
- [ ] Local and cloud/remote-storage providers.
- [ ] Existing STRM scanner/importer.
- [ ] Kodi-specific M3U compatibility profile.
- [ ] Formal plugin capability contracts, registry, metadata validation, dynamic loading,
  isolation, and third-party SDK.
- [ ] WebSockets or Server-Sent Events if polling proves insufficient.

## Superseded or no longer aligned

- [x] Post-1.0 “Emby adapter” placeholder: superseded by the built-in polling adapter.
- [x] Generic future “runtime proxy mode”: superseded for Live TV by the v0.10.0 live
  gateway; only optional VOD proxying remains.
- [x] NextPVR as a required aggregation hop: no longer aligned with the accepted client-
  DVR ownership boundary.
- [x] Direct Channels `/dvr/*` configuration automation: rejected unless Channels
  publishes a supported contract; v0.11.0 uses consumer-facing standards instead.

## Known client behavior

- Emby and Channels DVR consume the generated Live M3U successfully.
- Kodi IPTV Simple can play the output but may apply its own channel order or repeated-
  placement behavior; the same behavior occurs with the original IPTV Boss playlist.
- Unmapped Emby VOD playback can reserve and promote through its STRM runtime request,
  but polling cannot release it promptly unless the Emby item is correlated to the
  Media Router catalog item. Exact VOD mappings are currently item-level and manual.
