# Database

## Current Status

Sprint 3 extends the SQLite catalog schema with provider, account/connection, and source availability records while preserving the Sprint 1.5 JSON-backed foundation state.

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
- `/data/media_router.db`

The JSON files remain the foundation settings store. `media_router.db` stores catalog identity, providers, accounts/connections, source availability, legacy source mappings, and import history.

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

This table answers "where is this catalog item available?" only. The Broker will later decide which source to use.

Large playlist imports stream M3U input line by line and batch commits periodically to avoid loading entire playlists into memory.

### active_streams

Tracks broker reservations.

Important fields:

- `id`
- `reservation_id`
- `catalog_item_id`
- `account_id`
- `client_host`
- `client_user_agent`
- `started_at`
- `expires_at`
- `released_at`

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
