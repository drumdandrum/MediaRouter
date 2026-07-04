# API

## Current Sprint 2 API

The current project exposes Sprint 2 catalog foundation endpoints. Broker, accounts, outputs, and media integrations are deliberately deferred.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Confirms the app process is ready. |
| `GET` | `/api/foundation` | Returns project phase and module metadata. |
| `GET` | `/api/dashboard` | Returns Sprint 1 dashboard summary. |
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
| `GET` | `/api/catalog/live` | Lists live channel catalog records. |
| `GET` | `/api/catalog/movies` | Lists movie catalog records. |
| `GET` | `/api/catalog/series` | Lists series catalog records. |
| `GET` | `/api/catalog/episodes` | Lists episode catalog records. |
| `GET` | `/api/catalog/sources` | Lists source mappings with provider credentials redacted from URLs. |
| `POST` | `/api/catalog/import` | Queues a catalog import job for M3U file paths. |
| `POST` | `/api/catalog/clear-test-data` | Clears catalog test/import data for development. |

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
- `POST /api/accounts/test`
- `POST /api/accounts/{id}/disable`
- `POST /api/accounts/{id}/enable`

### Catalog

- `GET /api/catalog/summary`
- `GET /api/catalog/live`
- `GET /api/catalog/movies`
- `GET /api/catalog/series`
- `GET /api/catalog/episodes`
- `GET /api/catalog/sources`
- `POST /api/catalog/import`
- `POST /api/catalog/clear-test-data`

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
