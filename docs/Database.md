# Database

## Current Status

The foundation phase does not create a SQLite schema. Sprint 1.5 persists foundation state as JSON files in the configured data directory.

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

This is the foundation settings store. SQLite should be introduced later with migrations when the database phase begins.

Persistence beyond the foundation settings store should be added only after the Settings and Accounts contracts are accepted.

SQLite is the preferred first database because Media Router is initially a single-server home application. The schema should still be designed cleanly enough to migrate to another relational database later.

## Database Principles

- Use migrations from the first persisted schema.
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

### accounts

Stores IPTV provider account metadata.

Important fields:

- `id`
- `provider_name`
- `server_url`
- `username`
- `password_secret_ref`
- `maximum_streams`
- `priority`
- `enabled`
- `user_agent`
- `headers_json`
- `timeout_seconds`
- `health_status`
- `failure_count`

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

1. Add migration tooling.
2. Create settings and path mapping tables.
3. Add accounts and secret reference model.
4. Add catalog and source tables.
5. Add stream reservations.
6. Add imports and output plugin state.
7. Add events/logging.

## Open Decisions

- Whether local secrets are encrypted in SQLite or delegated to a secret provider.
- Whether import previews are persisted permanently or treated as temporary job data.
- How aggressively active stream history is retained.
- What duplicate-detection strategy is used before assigning a new internal ID.
