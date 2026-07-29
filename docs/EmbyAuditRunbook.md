# Emby Mapping-Audit Operator Runbook

This runbook covers the preview-only endpoint:

```text
POST /api/integrations/emby/mapping-audit/preview
```

The endpoint reads Emby and Media Router catalog state to produce diagnostics. It
does not apply mappings, create VOD crosswalks, change playback identity, or mutate
reservations, bindings, or lifecycle state.

## Operating policy

- Run one media type per request.
- Use this recommended order: `channel`, `series`, `episode`, then `movie`.
- Prefer lower-load periods for episode and movie scans.
- Do not schedule frequent polling or unattended audit automation.
- Never treat a truncated response as a complete library report.
- Review returned free text for sensitive material before storing or sharing it.
- Do not use this endpoint as an application workflow. VOD results are diagnostic
  and always ineligible for application.

## Before the audit

Check API health and record the running container identity:

```sh
curl -fsS http://127.0.0.1:8088/api/health
docker inspect media-router \
  --format 'id={{.Id}} status={{.State.Status}} started={{.State.StartedAt}} image={{.Image}}'
docker stats --no-stream media-router
```

Record supported read-only state metrics appropriate to the deployment, including:

- mapping count and deterministic digest;
- reservation count;
- Emby binding count;
- recent application errors;
- container CPU, memory, ID, and start time.

Do not print database credentials, Emby API keys, provider URLs, or container
environment values.

### Deterministic mapping digest

The mapping table has a composite primary key. Ordering by only the first column is
not deterministic when many rows share that value. The following command opens the
database in read-only mode, selects an explicit column sequence, orders by the
complete primary key, and hashes canonical JSON:

```sh
docker exec media-router python -c 'import hashlib,json,sqlite3; db=sqlite3.connect("file:/data/media_router.db?mode=ro",uri=True); rows=db.execute("SELECT emby_server_id,integration_id,emby_item_id,emby_media_source_id,emby_channel_name,catalog_item_id,mapping_source,created_at,updated_at FROM emby_channel_mappings ORDER BY emby_server_id,emby_item_id").fetchall(); print(len(rows),hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(",",":")).encode()).hexdigest())'
```

Run exactly the same command before and after the audit. A count or digest change
requires investigation; do not attribute it to the audit until concurrent supported
mapping activity has been ruled out. The command performs no database writes.

## Preview requests

The request `limit` bounds returned detail rows only. It does not bound how many
selected Emby items are scanned to calculate aggregates. The service scans up to
10,000 items with bounded remote pages.

Channel:

```sh
curl -fsS -X POST http://127.0.0.1:8088/api/integrations/emby/mapping-audit/preview \
  -H 'Content-Type: application/json' \
  --data '{"media_types":["channel"],"offset":0,"limit":25}'
```

Series:

```sh
curl -fsS -X POST http://127.0.0.1:8088/api/integrations/emby/mapping-audit/preview \
  -H 'Content-Type: application/json' \
  --data '{"media_types":["series"],"offset":0,"limit":25}'
```

Episode:

```sh
curl -fsS -X POST http://127.0.0.1:8088/api/integrations/emby/mapping-audit/preview \
  -H 'Content-Type: application/json' \
  --data '{"media_types":["episode"],"offset":0,"limit":25}'
```

Movie:

```sh
curl -fsS -X POST http://127.0.0.1:8088/api/integrations/emby/mapping-audit/preview \
  -H 'Content-Type: application/json' \
  --data '{"media_types":["movie"],"offset":0,"limit":25}'
```

Issue only the intended request. Do not combine media types merely to reduce the
number of calls.

## Interpret the response

- `scanned_count` is the selected population actually classified.
- `returned_count` is the number of detail rows returned after `offset` and `limit`.
- `scan_complete=true` and `truncated=false` indicate that the selected scan
  completed.
- `scan_complete=false` and `truncated=true` indicate that the safety cap was
  reached. All aggregate and collision totals are partial.
- Classification totals must reconcile with `scanned_count`.
- Evidence totals count classified rows that reported evidence; unmatched,
  ambiguous, and unsupported rows may have no evidence source.
- `apply_eligible` is descriptive only. The preview endpoint never applies a result,
  and movie, series, and episode rows are always ineligible.

Inspect returned details and diagnostics for unexpected URLs, filesystem paths,
tokens, credentials, or raw provider data. Stop and retain the response securely if
any sensitive material appears.

## Resource expectations

The audit fetches bounded pages but currently retains the scanned items, normalized
items, classified details, counters, and selected catalog indexes in memory until the
response is built. Resource use therefore grows with both the scanned Emby population
and relevant catalog data.

Production validation observed:

- series: 16 items in 0.587 seconds;
- episodes: 1,596 items in 5.362 seconds, with a transient peak near one CPU core
  and 721.5 MiB;
- movies: 10,000 items in 41.424 seconds, truncated at the cap, with about 84% peak
  CPU and 177.5 MiB.

Do not infer a universal memory ratio from these samples: Emby DTO size, catalog
indexes, collision sets, and Python object overhead differ by media type.

## After the audit

Repeat the health, container, resource, mapping count/digest, reservation, binding,
and recent-error checks. Confirm:

- API health remains ready;
- container ID and start time are unchanged;
- mapping count and deterministic digest are unchanged, absent explained concurrent
  supported activity;
- reservations and Emby bindings are unchanged by the audit;
- no playback or lifecycle action occurred;
- no new application errors appeared;
- no sensitive output was exposed.

If health degrades, the container restarts, integrity checks change unexpectedly, or
sensitive output appears, stop further audit requests and investigate.

## Future hardening direction

A complete movie audit should not be implemented by merely raising the 10,000-item
cap. Prefer a server-issued continuation cursor tied to a stable audit snapshot or
session. Each bounded chunk should record its Emby continuation position, catalog
snapshot/version, cumulative sanitized aggregates, and completion state. Operators
could resume interrupted scans and export chunked aggregate/detail artifacts without
holding the full library in one request or process.

Episode memory can be reduced by streaming classification and aggregate updates while
retaining only the requested detail window. Duplicate-name and structural-identity
decisions require a bounded first pass or compact count indexes, followed by a second
classification pass or resumable spool. This optimization is deferred and must
preserve the endpoint's read-only and conservative matching guarantees.
