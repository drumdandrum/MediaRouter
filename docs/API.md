# API

## Current Sprint 5 API

The current project exposes Sprint 5 source resolution runtime endpoints on top of Sprint 4 broker decisions and reservations. Runtime URLs redirect to selected provider/source URLs; playback, proxy streaming, transcoding, STRM output, HDHomeRun output, and media integrations are deliberately deferred.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Confirms the app process is ready. |
| `GET` | `/api/foundation` | Returns project phase and module metadata. |
| `GET` | `/api/dashboard` | Returns dashboard summary, including provider/account and broker reservation counts. |
| `GET` | `/api/settings` | Reads JSON-backed foundation settings. |
| `PUT` | `/api/settings` | Updates JSON-backed foundation settings. |
| `GET` | `/api/wizard/steps` | Lists Sprint 1 wizard steps. |
| `GET` | `/api/wizard/state` | Reads persisted wizard state. |
| `PUT` | `/api/wizard/state` | Updates persisted wizard state. |
| `POST` | `/api/wizard/steps/{step_id}/complete` | Marks a wizard step complete. |
| `GET` | `/api/jobs` | Lists persistent foundation job history. |
| `POST` | `/api/jobs` | Starts a background test/foundation job and records history. |
| `GET` | `/api/jobs/{job_id}` | Reads one job. |
| `GET` | `/api/logs` | Lists sanitized in-memory logs. |
| `POST` | `/api/logs` | Adds a sanitized log entry. |
| `GET` | `/api/system` | Shows app version, environment, Git, database, and Docker/container status. |
| `GET` | `/api/catalog/summary` | Returns catalog counts and last import time. |
| `GET` | `/api/catalog/items?limit=200&offset=0` | Lists bounded catalog items for UI selection/testing. |
| `GET` | `/api/catalog/live?limit=100&offset=0` | Lists paginated live channel catalog records. |
| `GET` | `/api/catalog/movies?limit=100&offset=0` | Lists paginated movie catalog records. |
| `GET` | `/api/catalog/series?limit=100&offset=0` | Lists paginated series catalog records. |
| `GET` | `/api/catalog/episodes?limit=100&offset=0` | Lists paginated episode catalog records. |
| `GET` | `/api/catalog/sources?limit=100&offset=0` | Lists paginated source mappings with provider credentials redacted from URLs. |
| `GET` | `/api/catalog/{id}/sources?limit=100&offset=0` | Lists paginated availability records for one catalog item. |
| `POST` | `/api/catalog/import` | Queues a streaming M3U import for a playlist path/URL, optionally tied to provider/account. |
| `POST` | `/api/catalog/clear-test-data` | Clears catalog test/import data for development. |
| `GET` | `/api/providers` | Lists providers. |
| `POST` | `/api/providers` | Creates a provider. |
| `GET` | `/api/providers/{id}` | Reads one provider. |
| `PUT` | `/api/providers/{id}` | Updates one provider. |
| `DELETE` | `/api/providers/{id}` | Deletes one provider and its accounts. |
| `GET` | `/api/accounts` | Lists accounts/connections with secret values hidden. |
| `POST` | `/api/accounts` | Creates an account/connection; playlist paths/URLs belong to catalog import, not accounts. |
| `GET` | `/api/accounts/{id}` | Reads one account/connection with secret values hidden. |
| `PUT` | `/api/accounts/{id}` | Updates one account/connection; blank password leaves the stored secret unchanged and reads never return the secret. |
| `DELETE` | `/api/accounts/{id}` | Deletes one account/connection. |
| `POST` | `/api/accounts/{id}/test` | Runs a lightweight non-streaming connection test. |
| `GET` | `/api/sources?limit=100&offset=0` | Lists paginated source availability records. |
| `PUT` | `/api/sources/{id}` | Updates source availability flags/notes. |
| `DELETE` | `/api/sources/{id}` | Deletes one source availability record. |
| `GET` | `/api/broker/status` | Returns reservation counts and account capacity usage. |
| `GET` | `/api/broker/reservations` | Lists recent broker reservations. |
| `POST` | `/api/broker/resolve` | Chooses the best available source and creates a temporary reservation. |
| `POST` | `/api/broker/release` | Releases one active reservation. |
| `POST` | `/api/broker/release-all` | Releases every active reservation. |
| `POST` | `/api/broker/expire-now` | Expires stale reservations immediately for testing. |
| `GET` | `/api/runtime/preview/{catalog_item_id}` | Returns the stable runtime URL, debug URL, catalog item, media type, and enabled source count. |
| `GET` | `/r/live/{catalog_item_id}` | Resolves a live channel through the Broker and returns HTTP `302` to the selected source URL. |
| `GET` | `/r/movie/{catalog_item_id}` | Resolves a movie through the Broker and returns HTTP `302` to the selected source URL. |
| `GET` | `/r/episode/{catalog_item_id}` | Resolves an episode through the Broker and returns HTTP `302` to the selected source URL. |

Interactive OpenAPI docs are available at `/docs` while the server is running.

`GET /api/foundation` includes:

- Project name.
- Current phase.
- Implementation scope.
- Binding principles.
- Module descriptors.

## API Principles

- Feature APIs are introduced module by module.
- Read responses must not expose passwords, API keys, tokens, or provider credentials.
- Mutating APIs should return clear summaries of what changed.
- Import APIs should support preview before destructive changes.
- Long-running jobs should use job records rather than blocking HTTP requests.
- Broker stream endpoints should remain stable even if provider data changes.

## Planned API Groups

### Settings

- `GET /api/settings`
- `PUT /api/settings`
- `GET /api/settings/categories`
 
Deferred:

- `GET /api/path-mappings`
- `POST /api/path-mappings`
- `PUT /api/path-mappings/{id}`
- `DELETE /api/path-mappings/{id}`

### Wizard

- `GET /api/wizard/steps`
- `GET /api/wizard/state`
- `PUT /api/wizard/state`
- `PUT /api/wizard/values`
- `POST /api/wizard/steps/{step_id}/complete`

### Discovery

- `POST /api/discovery/scan`
- `GET /api/discovery/results/{scan_id}`

### Accounts

- `GET /api/accounts`
- `POST /api/accounts`
- `GET /api/accounts/{id}`
- `PUT /api/accounts/{id}`
- `DELETE /api/accounts/{id}`
- `POST /api/accounts/{id}/test`

### Providers

- `GET /api/providers`
- `POST /api/providers`
- `GET /api/providers/{id}`
- `PUT /api/providers/{id}`
- `DELETE /api/providers/{id}`

### Catalog

- `GET /api/catalog/summary`
- `GET /api/catalog/live`
- `GET /api/catalog/movies`
- `GET /api/catalog/series`
- `GET /api/catalog/episodes`
- `GET /api/catalog/sources`
- `GET /api/catalog/{id}/sources`
- `POST /api/catalog/import`
- `POST /api/catalog/clear-test-data`

### Source Availability

- `GET /api/sources`
- `PUT /api/sources/{id}`
- `DELETE /api/sources/{id}`

### Broker

- `GET /api/broker/status`
- `GET /api/broker/reservations`
- `POST /api/broker/resolve`
- `POST /api/broker/release`
- `POST /api/broker/release-all`
- `POST /api/broker/expire-now`

### Runtime Resolution

- `GET /api/runtime/preview/{catalog_item_id}`
- `GET /r/live/{catalog_item_id}`
- `GET /r/movie/{catalog_item_id}`
- `GET /r/episode/{catalog_item_id}`

### Outputs

- `GET /api/outputs`
- `POST /api/outputs/{plugin_name}/build`
- `GET /api/outputs/{plugin_name}/status`

### HDHomeRun Output

- `GET /discover.json`
- `GET /lineup.json`
- `GET /lineup_status.json`

## Error Shape

Feature APIs should use a consistent error shape:

```json
{
  "error": {
    "code": "account_unhealthy",
    "message": "No healthy account has stream capacity.",
    "details": {}
  }
}
```

FastAPI validation errors may keep their standard shape unless a custom API envelope is adopted later.

## Sprint 3 Import Notes

Catalog import accepts local/container paths such as `/iptvboss/outputs/THREAD1.m3u`, sample paths such as `sample_data/live.m3u`, and HTTP/HTTPS playlist URLs. Accounts describe access/authentication; playlist path/URL association happens during Catalog Import.

If the playlist field is blank, the API returns `400`. If a local/container path is not visible to the app process, the API returns `404` before creating an import job.

Catalog import also validates provider/account pairing before creating a job. Missing provider, missing account, unsupported media type, or an account that belongs to another provider returns `400`; unreadable local files return `403`.

### Catalog Import Manual UI Checklist

1. Create a provider.
2. Create an account under that provider.
3. Select the provider on Catalog Import.
4. Confirm the Account / Connection list filters to accounts for the selected provider.
5. Submit an import and confirm the request includes `provider_id` and `account_id`.

## Sprint 4 Broker Notes

Sprint 4 does not play, proxy, transcode, redirect, or validate streams. The Broker only chooses a source availability record for a catalog item and reserves account capacity for a short TTL.

`POST /api/broker/resolve` accepts:

```json
{
  "catalog_item_id": "channel_abc123",
  "media_type": "live",
  "client_label": "Manual broker test",
  "reservation_ttl_seconds": 60
}
```

The response includes the selected source, provider, account/connection, redacted stream/location reference, reservation ID, expiration time, TTL, decision reason, and evaluated candidates.

Selection policy:

- Enabled sources only.
- Enabled providers only.
- Enabled accounts/connections only.
- Accounts with `Authentication Failed`, `Playlist Failed`, `Offline`, or `Disabled` health are excluded.
- Active reservations count against account capacity across all media types.
- Preferred priority groups are selected before Secondary, then Emergency.
- Higher account weight wins within a priority group.
- Lower active reservation count wins after priority and weight.
- Stable deterministic tie-breaks are used after policy fields.

Each evaluated candidate includes selected/skipped state, account, provider, priority, weight, usage, health, and a human-readable reason such as `selected`, `lower weight`, `at capacity`, `account disabled`, `provider disabled`, or `unhealthy`.

If no source is available, the API returns `409` with a structured detail code such as `no_sources`, `all_disabled`, `all_unhealthy`, or `all_at_capacity`. The error detail includes `failure_code`, `failure_message`, `decision_reasons`, and `evaluated_candidates` so the UI can show friendly diagnostics instead of raw objects.

`POST /api/broker/release-all` releases every active reservation and returns refreshed broker status. `POST /api/broker/expire-now` marks expired active reservations and returns refreshed broker status.

## Sprint 5 Runtime Notes

Sprint 5 adds stable Media Router URLs that clients can call instead of direct provider URLs:

- `/r/live/{catalog_item_id}`
- `/r/movie/{catalog_item_id}`
- `/r/episode/{catalog_item_id}`

User-facing runtime previews use this base URL priority:

1. Settings > Runtime > Runtime Public Base URL.
2. `MEDIA_ROUTER_PUBLIC_BASE_URL`, when it is set to a client-visible hostname.
3. Current request-derived scheme and host.

The Docker-internal hostname `media-router` is not shown in Catalog or Broker runtime previews. For local testing, set Runtime Public Base URL to `http://localhost:8088`.

Default behavior is redirect mode. A request such as `GET /r/movie/{id}` calls the Broker, creates a temporary reservation, respects disabled providers/accounts/sources and account capacity, then returns HTTP `302` with the selected provider/source URL in the `Location` header.

Debug mode is enabled with `debug=true`. A request such as `GET /r/movie/{id}?debug=true` creates the same reservation but returns JSON instead of redirecting. The response includes the catalog item, selected provider, selected account, selected source, reservation ID, expiration time, redacted stream/location reference, Broker decision reason, and evaluated candidates.

Runtime routes accept optional query parameters:

- `ttl=300` to set reservation TTL seconds for that resolve request.
- `client_label=emby-test` to store a client label on the reservation.
- `debug=true` to return JSON diagnostics rather than redirecting.

If no source is available, runtime routes return a structured error with `failure_code`, `failure_message`, `decision_reasons`, and `evaluated_candidates`. Expected failure codes include `catalog_item_not_found`, `no_sources`, `all_disabled`, `all_unhealthy`, and `all_at_capacity`.

Sprint 5 does not proxy, play, transcode, generate STRM files, emulate HDHomeRun, or sync with Emby/Jellyfin/Channels. Future output plugins should use these stable Media Router URLs.
