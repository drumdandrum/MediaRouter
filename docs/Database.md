# Database

## Current Status

Sprint 4 extends the SQLite catalog schema with broker reservations while preserving the Sprint 1.5 JSON-backed foundation state and Sprint 2/3 catalog, provider, account, and source availability records. Sprint 5 adds runtime resolve routes on top of the existing broker reservations table. Sprint 6 adds STRM output metadata tables while generated `.strm` files remain disposable artifacts. Sprint 7 adds JSON-backed Live TV M3U output settings and uses the existing output history/tracking metadata; generated `.m3u` files remain disposable artifacts.

v0.8.1 adds `playback_identity_key` to `broker_reservations`. The value is a privacy-safe hash scoped to catalog item, normalized media type, and hashed explicit session or derived fingerprint. A partial unique index, `uq_broker_active_playback_identity`, permits only one active row per playback key. Upgrade reconciliation releases pre-existing duplicates before creating the index and preserves all historical rows.

`broker_reservation_identity_aliases` stores scoped hashed identities observed for a reservation: `alias_id`, `reservation_id`, `catalog_item_id`, `media_type`, `identity_type`, `identity_hash`, hashed origin, request-profile classification, active state, and first/last-seen timestamps. Active aliases are unique per catalog item, media type, identity type, and hash. Releasing or expiring a reservation deactivates its aliases; historical rows remain available for diagnostics.

Reservations also retain the primary stable-client hash when present, hashed origin, request profile, and `coalesced_reuse_count`. Provider locations and credentials are not stored in alias records.

Docker Compose mounts:

```text
./data:/data
```

The container uses:

```text
MEDIA_ROUTER_DATA_DIR=/data
```

Current persisted files:

- `/data/settings.json`
- `/data/wizard_state.json`
- `/data/jobs.json`
- `/data/outputs_strm_settings.json`
- `/data/outputs_live_m3u_settings.json`
- `/data/media_router.db`

The JSON files remain the foundation and output settings stores. `media_router.db` stores catalog identity, providers, accounts/connections, source availability, legacy source mappings, import history, broker reservations, and generated output metadata.

SQLite is the preferred first database because Media Router is initially a single-server home application. The schema should still be designed cleanly enough to migrate to another relational database later.

## Database Principles

- Add migrations before the schema grows beyond the Sprint 2 bootstrap tables.
- Keep provider credentials out of normal read models.
- Store host paths and Docker paths as separate fields.
- Treat catalog internal IDs as stable public identifiers.
- Enforce exactly one internal ID per movie, episode, or live channel.
- Store provider sources as mappings to catalog items, not as catalog identity.
- Never use provider URLs as permanent identity.
- Keep output plugin state separate from core catalog state.
- Do not expose database sessions to plugins.
- Treat generated outputs as disposable; persisted output state is metadata, not the output itself.

## Proposed Tables

### settings

Stores application-level configuration.

Important fields:

- `key`
- `value`
- `value_type`
- `is_secret`
- `created_at`
- `updated_at`

### path_mappings

Stores host/container path relationships.

Important fields:

- `id`
- `label`
- `host_path`
- `container_path`
- `purpose`
- `exists_last_checked`

### providers

Stores provider/origin records.

Important fields:

- `id`
- `friendly_name`
- `provider_type`
- `notes`
- `enabled`
- `health_status`
- `created_at`
- `updated_at`

Provider types are generic: IPTV, HDHomeRun, NextPVR, Local Files, Emby, Jellyfin, and Other.

### accounts

Stores provider account/connection metadata.

Important fields:

- `id`
- `provider_id`
- `friendly_name`
- `username`
- `password_secret`
- `base_url`
- `max_simultaneous_streams`
- `priority_group`
- `weight`
- `enabled`
- `health_status`
- `last_success`
- `last_failure`
- `notes`
- `created_at`
- `updated_at`

Sprint 3 keeps password/secret reads redacted. Local encryption is deferred and tracked as a hardening decision. Existing `playlist_url` columns from early Sprint 3 builds are deprecated and ignored; playlists are associated through catalog imports.

### catalog_items

Stores permanent internal media records.

Important fields:

- `id`
- `internal_id`
- `media_kind`
- `title`
- `sort_title`
- `group_title`
- `tvg_id`
- `tvg_name`
- `tvg_logo`
- `tvg_chno`
- `season_number`
- `episode_number`
- `external_ids_json`
- `logo_url`
- `created_at`
- `updated_at`

Sprint 2 implemented fields also include:

- `cuid`
- `parent_internal_id`
- `confidence`
- `raw_title`

Sprint 7 preserves imported live TV channel numbers in `tvg_chno` when M3U `#EXTINF` metadata includes `tvg-chno`, `channel-number`, or `chno`. Live TV M3U output emits `tvg-chno` and a stable `media-router-id`, adds the same identity as `mr_catalog_id` on the credential-free runtime URL, and sorts by numeric channel number when available, then group title, then display title.

Constraints:

- `internal_id` is unique.
- A future matching strategy should prevent duplicate internal IDs for the same movie, episode, or channel.

### catalog_sources

Maps catalog items to one or more provider source URLs.

Important fields:

- `id`
- `catalog_item_id`
- `account_id`
- `source_url`
- `source_type`
- `last_seen_at`
- `health_status`

Multiple source rows may point to the same catalog item. This is how account failover and source balancing should be modeled.

### channel_placements

Stores Live TV editorial membership separately from canonical `catalog_items` identity. One channel/CUID may have many active placements, each with:

- `placement_id`
- `catalog_item_id`
- `source_identity`, `source_name`, and `source_playlist`
- `import_job_id` and internal `import_run_id`
- `group_title`, `channel_number`, and `display_title`
- `placement_index` preserving source-playlist order
- `tvg_id`, `tvg_name`, and `tvg_logo`
- `active`, `created_at`, and `updated_at`

`UNIQUE(source_identity, placement_index)` makes re-import deterministic while allowing the same `catalog_item_id` at multiple positions or in multiple groups. Successful re-import upserts current positions and marks absent positions inactive. The additive bootstrap migration backfills one `legacy-canonical` placement per existing channel; that fallback is ignored once real placements exist for the channel. No catalog, source, account, or reservation rows are removed.

Sprint 2 stores source mappings separately from catalog identity records. Broker/account failover is not implemented. Source URLs are stored for future routing, but API/UI read models redact credential path segments.

### source_availability

Connects catalog identity to provider/account availability.

Important fields:

- `id`
- `catalog_internal_id`
- `provider_id`
- `account_id`
- `external_id`
- `location_ref`
- `media_type`
- `enabled`
- `last_seen_at`
- `metadata_confidence`
- `notes`
- `raw_extinf`
- `created_at`
- `updated_at`

This table answers "where is this catalog item available?" only. The Broker decides which source to use, and Sprint 5 runtime URLs call that Broker decision layer.

Large playlist imports stream M3U input line by line and batch commits periodically to avoid loading entire playlists into memory.

### broker_reservations

Tracks Sprint 4 broker decisions and temporary account reservations. Sprint 5 runtime resolve routes also write reservations here before redirecting. This table does not represent active playback or proxy sessions.

Important fields:

- `id`
- `reservation_id`
- `catalog_item_id`
- `source_availability_id`
- `provider_id`
- `account_id`
- `media_type`
- `location_ref`
- `status`
- `generation_run_id` (nullable batch-run marker used for incremental STRM tracking and safe cleanup)
- `created_at`
- `expires_at`
- `released_at`
- `client_label`
- `client_session`
- `client_fingerprint`

`lifecycle_state` values are `provisional`, `active`, `released`, `expired`, `superseded`, and legacy `failed`. Only provisional and active rows consume account capacity. Additive lifecycle columns record provisional/active expiries, promotion and supersession relationships, first/last activity, request counters, policy TTL, and terminal reasons. Legacy `status` and `expires_at` remain compatibility mirrors. Upgrade maps existing non-expired active rows to lifecycle active without changing reservation IDs.

The Emby adapter additively adds `last_confirmation_source` and `last_confirmed_at` to reservations. `emby_playback_bindings` maps exactly one `(Emby server ID, Emby session ID)` consumer lifecycle to a reservation and records ItemId, MediaSourceId, catalog identity, observation/missing/release timestamps, state, sanitized display metadata, and correlation method/confidence. `PlaySessionId`, server address, and device origin never own or merge bindings. A partial unique index permits one unreleased binding per Emby binding key. Reservation capacity is never counted from this table. `emby_observed_sessions` contains only the current bounded normalized diagnostic representation; complete Emby responses are never stored. `runtime_correlation_observations` holds capacity-neutral, privacy-safe hints from reserving runtime GETs for 120 seconds. Correlation considers them for a 90-second candidate window; identity values are hashed, and provider URLs, credentials, and headers are not stored. Initialization is idempotent and does not rewrite existing reservation rows.

`emby_channel_mappings` is an additive channel crosswalk keyed by Emby server/integration ID and TvChannel ItemId. It stores an optional MediaSourceId, display name, nullable Media Router catalog item, mapping source (`manual`, `automatic_marker`, `automatic_title`, or `unmapped`), and creation/update timestamps. Live M3U output carries the catalog ID in a dedicated `media-router-id` attribute and deterministic runtime URL. Playback resolution prioritizes manual mappings, automatic-marker mappings, other exact ItemId mappings, then exact MediaSourceId mappings; mapping rows never consume capacity themselves.

Runtime playback reservations currently release by TTL expiration. Manual Broker tests default to a short 60-second TTL; runtime live/movie/episode routes default to four hours unless a `ttl` query parameter is supplied. Client heartbeat and client-driven playback-end release are deferred.

Runtime requests store hashed reuse identity when available. `client_session` stores the hash of an explicit playback session; `client_fingerprint` stores a hash derived from stable fallback request attributes. These values correlate repeated playback requests without persisting raw session values or client headers.

v0.8.1 hashes explicit sessions and derived fingerprints before persistence. `identity_type`, `last_seen_at`, `last_action`, and `reuse_count` support diagnostics. Matching active identities are reused for the reservation lifetime. SQLite `BEGIN IMMEDIATE` serializes lookup and creation so concurrent requests cannot create parallel reservations for one identity.

### imports

Tracks import jobs and previews.

Important fields:

- `id`
- `source_kind`
- `source_path`
- `status`
- `summary_json`
- `started_at`
- `finished_at`

Sprint 2 uses `catalog_imports` with:

- `job_id`
- `source_name`
- `file_path`
- `status`
- `summary_json`
- `imported_at`

### outputs

Stores enabled output plugin instances.

Important fields:

- `id`
- `plugin_name`
- `enabled`
- `config_json`
- `last_build_status`
- `last_build_at`

Generated output files are not authoritative state. They should be reproducible from catalog, settings, and plugin configuration.

### output_generated_files

Tracks generated output files so regeneration, history display, and STRM orphan cleanup can be safe. Sprint 6 uses this for movie/episode STRM files; Sprint 7 records the generated Live TV M3U file as disposable output metadata.

Important fields:

- `output_id`
- `catalog_item_id`
- `media_type`
- `output_type`
- `output_path`
- `last_content_hash`
- `last_generated_at`

### output_run_history

Tracks recent output runs for UI/job diagnostics.

Important fields:

- `id`
- `output_id`
- `output_type`
- `mode`
- `status`
- `summary_json`
- `message`
- `created_at`

Sprint 6 STRM and Sprint 7 Live TV M3U runs both write summaries here. The generated output files themselves are not authoritative state.

v0.8.1 continues to persist STRM settings in `/data/outputs_strm_settings.json`. Missing generation fields deserialize to Test mode, 500 movies, 500 episodes, batch size 250, and 4 file workers. Each batch prefetches its tracking hashes, bulk-upserts generated-file records with `executemany`, and commits once instead of using per-file commits or one catalog-sized transaction.

Live M3U settings remain in `/data/outputs_live_m3u_settings.json`. v0.8.1 adds `generation_mode` and `maximum_live_channels`; saved files without these fields safely deserialize to Test/500. A positive legacy `channel_limit` is interpreted as a Custom limit, while a missing or zero legacy value becomes Test/500 rather than Unlimited. No SQLite schema change is required for Live limits.
- `status`

Only tracked generated files may be removed by orphan cleanup. The STRM file content is disposable and should contain Media Router runtime URLs, not provider URLs.

### output_run_history

Stores recent output run summaries.

Important fields:

- `output_id`
- `output_type`
- `mode`
- `status`
- `summary_json`
- `started_at`
- `finished_at`

### events

Stores user-facing operational diagnostics.

Important fields:

- `id`
- `level`
- `category`
- `message`
- `metadata_json`
- `created_at`

Messages and metadata must be scrubbed before insert.

## Migration Plan

1. Add migration tooling before Sprint 4 schema expansion.
2. Formalize the Sprint 2 and Sprint 3 bootstrap tables as migration `0001`.
3. Create settings and path mapping tables if JSON state moves into SQLite.
4. Add encrypted secret storage or secret-provider integration.
5. Add stream reservations.
6. Add output plugin state.
7. Add events/logging.

## Open Decisions

- Whether local secrets are encrypted in SQLite or delegated to a secret provider.
- Whether import previews are persisted permanently or treated as temporary job data.
- How aggressively active stream history is retained.
- What duplicate-detection strategy is used before assigning a new internal ID.
