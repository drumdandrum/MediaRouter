# API

## Current Sprint 3 API

The current project exposes Sprint 3 provider/account availability endpoints. Broker routing, failover, playback, outputs, and media integrations are deliberately deferred.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Confirms the app process is ready. |
| `GET` | `/api/foundation` | Returns project phase and module metadata. |
| `GET` | `/api/dashboard` | Returns dashboard summary, including provider/account availability counts. |
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

- `GET /movie/{internal_id}`
- `GET /series/{internal_id}`
- `GET /live/{internal_id}`
- `POST /api/broker/resolve`

### Streams

- `GET /api/streams`
- `DELETE /api/streams/{reservation_id}`

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
