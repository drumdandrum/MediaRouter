# Media Router

Media Router is currently a foundation, catalog engine, provider/account availability model, broker decision engine, source resolution runtime, STRM output generator, and Sprint 7 Live TV M3U output generator for a future Dockerized home media orchestration platform. The project intentionally does not implement stream playback, proxy streaming, transcoding, HDHomeRun output, XMLTV output, or DVR/media-server integration yet.

The goal of this phase is to lock down module ownership, boundaries, and delivery order before building features.

## How To Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8088
```

Open `http://localhost:8088`.

For local development on the same machine:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8088
```

## Run With Docker

```bash
docker compose up --build
```

The Docker build/runtime metadata uses:

```text
MEDIA_ROUTER_APP_VERSION=v0.8.1
MEDIA_ROUTER_GIT_BRANCH=main
MEDIA_ROUTER_GIT_COMMIT=<short commit>
```

The compose defaults are suitable for the current foundation build. To force current Git metadata during a rebuild:

```bash
MEDIA_ROUTER_GIT_BRANCH="$(git branch --show-current)" \
MEDIA_ROUTER_GIT_COMMIT="$(git rev-parse --short HEAD)" \
docker compose up --build -d
```

Persistent foundation data is stored in:

```text
./data
```

Docker Compose mounts that folder into the container as:

```text
/data
```

Settings, wizard state, and job history are written under this mounted path. They should survive `docker compose down` followed by `docker compose up -d`.

## What Exists Now

- Minimal FastAPI app.
- App version `v0.8.1`.
- Sprint 7 web console.
- `/api/health` endpoint.
- `/api/foundation` endpoint with module metadata.
- Dashboard, wizard, settings, and jobs APIs.
- Catalog summary/list/import APIs.
- Provider and account/connection APIs.
- Source availability APIs.
- Broker decision, reservation, release, and status APIs.
- Broker UI for account usage, reservation status, source decision testing, and evaluated candidate explanations.
- Stable runtime resolve URLs at `/r/live/{id}`, `/r/movie/{id}`, and `/r/episode/{id}`.
- Runtime preview API and Catalog/Broker UI runtime URL previews.
- STRM output settings, dry-run, generation job, history, and generated-file tracking for movies and episodes.
- Live TV M3U output settings, validation, dry-run, generation job, history, and preview.
- Paginated catalog/source APIs for large playlists.
- About/System and logs APIs.
- JSON-backed settings and wizard state.
- Persistent JSON-backed job history.
- SQLite-backed catalog identity, provider/account, and source availability database under `./data`.
- Sample M3U playlists under `sample_data/`.
- Domain-level types and contracts.
- Module folders with README ownership notes.
- Dockerfile and Docker Compose scaffold.
- Architecture documents in `docs/`.

## Documentation

- [Vision](docs/Vision.md)
- [Principles](docs/Principles.md)
- [Architecture](docs/Architecture.md)
- [Database](docs/Database.md)
- [API](docs/API.md)
- [Installation](docs/Installation.md)
- [Roadmap](docs/Roadmap.md)
- [Backlog](docs/Backlog.md)
- [Plugin SDK](docs/PluginSDK.md)
- [Decisions](docs/Decisions.md)

## What Is Deliberately Not Implemented Yet

- Playback or proxy streaming.
- Transcoding.
- STRM import or live TV STRM generation.
- IPTV Boss watching.
- HDHomeRun compatibility.
- Emby/Jellyfin/NextPVR/Channels adapters.
- XMLTV output.
- Encrypted secrets storage.
- Real stream playback.
- HDHomeRun output.
- Production dashboard UI.

Sprint 4 includes a decision-only Broker. It chooses the best source for a catalog item and creates a temporary account reservation. It does not play, proxy, transcode, or generate streams. Outputs, STRM, HDHomeRun, and media-server integrations are still deferred.

Sprint 4.1 polishes Broker diagnostics. Resolve results now show the selected account, provider, priority, weight, usage, TTL, selection reason, and every evaluated candidate with a selected/skipped reason.

Sprint 5 adds stable Media Router runtime URLs. Clients call Media Router URLs, Media Router asks the Broker for the current best source, creates a reservation, and returns an HTTP `302` redirect to the selected source. Debug mode (`?debug=true`) returns structured JSON with the catalog item, selected source/account/provider, reservation, and Broker explanation. Sprint 5 does not generate STRM files, emulate HDHomeRun, integrate with media servers, proxy streams, transcode, or play streams.

Runtime reservation acquisition is atomic. A committed active reservation is immediately reused for matching GET, HEAD, Range, reconnect, and slow-start retry requests. Deployments behind a trusted reverse proxy can enable proxy client headers in Runtime settings and select `x-forwarded-for`, `cf-connecting-ip`, or `x-real-ip`; headers remain untrusted by default.

When Emby changes request agents between probing and playback, v0.8.1 can attach the second derived fingerprint as an alias during a short startup window (90 seconds by default). The bounded fallback ignores User-Agent and requires exactly one recent active reservation for the same catalog item, media type, and persisted trusted-origin hash. Explicit sessions, different origins, expired windows, and ambiguous candidates are never coalesced.

Runtime playback reservations currently release by TTL expiration. Live, movie, and episode runtime URLs default to a four-hour reservation TTL; the `ttl=` query parameter can override this per request. Manual Broker/API decision tests keep the short 60-second default for diagnostics. Client heartbeat and explicit playback-end release are future work.

Runtime URLs support `GET` and `HEAD`. `HEAD` returns the same redirect `Location` as redirect-mode `GET` while reusing an active matching reservation or doing a non-reserving source lookup, so media-server checks do not consume extra account capacity. Redirect mode works for simple clients such as VLC and may work for Jellyfin/Emby, but some media servers may eventually require a future proxy mode.

Repeated runtime requests from the same playback identity are idempotent for the active reservation lifetime. Media Router uses explicit `client_session` when supplied, otherwise a hashed fingerprint derived from catalog item, media type, normalized client IP, and normalized User-Agent. Reused requests return the same redirect target and reservation ID without increasing account usage.

Runtime session correlation now reuses a matching active reservation until release or expiry. Explicit `client_session` is the strongest identity. Otherwise Media Router hashes a privacy-safe fingerprint of canonical client IP (never the source port), normalized User-Agent, catalog item, and media type. GET, HEAD, Range seeks, and reconnects do not change that identity. Weak fingerprints can collide when multiple clients behind the same NAT use the same player for the same item; explicit `client_session` should be used when callers can supply one.

Set Settings > Runtime > Runtime Public Base URL to the browser/client-visible address for runtime previews. For local testing, use:

```text
http://localhost:8088
```

If Runtime Public Base URL is blank, previews use `MEDIA_ROUTER_PUBLIC_BASE_URL` when it is not the Docker-internal `media-router` hostname, then fall back to the current request host/scheme.

Sprint 6 generates disposable `.strm` files for movies and episodes only. Each file contains a Media Router runtime URL such as `http://localhost:8088/r/movie/movie_abc123` or `http://localhost:8088/r/episode/episode_xyz789`; direct provider URLs and credentials are never written to STRM files. Configure container output paths on the Outputs page, for example `/outputs/movies` and `/outputs/series`.

For Docker, mount host folders into those container paths instead of entering Mac host paths in the UI:

```yaml
volumes:
  - /Users/Shared/IPTVBoss/output:/iptvboss/output:ro
  - /Users/Shared/MediaRouter/strm/movies:/outputs/movies
  - /Users/Shared/MediaRouter/strm/series:/outputs/series
  - /Users/Shared/MediaRouter/live:/outputs/live
```

The IPTVBoss import path can be read-only. STRM output paths must be read-write. Use Outputs > Validate Paths before Generate to confirm Movies and Series are writable and `/data` is writable.

v0.8.1 processes STRM catalogs in configurable batches (250 by default). Existing settings safely default to Test mode at 500 movies and 500 episodes; Small is 2,000/2,000, Medium is 5,000/10,000, and Custom requires positive limits. Unlimited must be explicitly selected and confirmed before Generate. Dry runs use the same limits and batching without writing files. Generation uses a bounded file worker pool (4 by default, configurable from 1–16), one tracking transaction per batch, cached directory creation, atomic replacement, and batch timing/throughput logs.

Sprint 7 generates disposable Live TV M3U playlists for live/channel catalog items only. Each channel URL points to a stable Media Router runtime URL such as `http://localhost:8088/r/live/channel_abc123`; direct provider URLs and credentials are never written to generated M3U files. Channel metadata preserves `tvg-chno`, `tvg-id`, `tvg-name`, `tvg-logo`, `group-title`, and display title when available. Configure the Live TV M3U output file on the Outputs page, for example `/outputs/live/live.m3u`, and use Validate Path before Dry Run or Generate.

v0.8.1 gives Live M3U the same safe generation modes as STRM: Test (500 eligible channels), Small (2,000), Medium (5,000), Custom (a positive user limit), and explicitly confirmed Unlimited. Dry-run, preview, and generation use the same availability filtering and cap. Catalog reads are paginated and ordered M3U content is streamed without exposing provider URLs.

Live editorial placement is separate from catalog identity. Repeated IPTV Boss entries with the same CUID remain one canonical channel and one `/r/live/{catalog_item_id}` runtime route, while each group/channel-number occurrence is stored as a placement and emitted separately in Live M3U output. Original playlist order is preferred, and re-import deactivates placements removed from that source playlist.

Guide XML is currently external from Media Router and can continue to come from IPTV Boss or a separate webserver. Sprint 7 does not generate XMLTV.

Secrets note: Sprint 3 stores account secrets locally in SQLite and never returns them through normal API reads or displays them in the UI. Encryption is a future hardening task before production-style secret handling.

## Sprint 1 Acceptance Test

- [ ] App starts locally on port `8088`.
- [ ] Sidebar shows app version `v0.2.1`.
- [ ] Dashboard shows Ready/Needs setup/Not configured labels, not raw status codes.
- [ ] Wizard shows Welcome, Environment, Paths, Services, Review, Complete.
- [ ] Wizard accepts manual placeholder values.
- [ ] Wizard completion persists after restart.
- [ ] Settings are grouped into General, Storage, Network, Services, Advanced.
- [ ] Settings include host path and container path fields.
- [ ] Settings persist after restart.
- [ ] Test job shows queued, running, and completed states.
- [ ] Logs page formats sanitized log entries.
- [ ] About page shows version, environment, branch, commit, database, Docker, and container status.
- [ ] No broker, STRM, HDHomeRun, media integrations, or streaming are present.

## Sprint 1.5 Persistence Acceptance Test

- [ ] Start with `docker compose up -d`.
- [ ] Open `http://localhost:8088`.
- [ ] Save settings in the Settings page.
- [ ] Complete at least one wizard step.
- [ ] Run a test job and wait for completion.
- [ ] Run `docker compose down`.
- [ ] Run `docker compose up -d`.
- [ ] Verify saved settings remain.
- [ ] Verify wizard completion state remains.
- [ ] Verify completed job history remains.
- [ ] Verify persistent files exist under `./data`.
- [ ] Verify settings persistence still passes after version/About cleanup.

## Sprint 2 Catalog Acceptance Test

- [ ] Start Docker with `docker compose up -d`.
- [ ] Open `http://localhost:8088`.
- [ ] Go to Catalog.
- [ ] Import:
  - `sample_data/live.m3u`
  - `sample_data/movies.m3u`
  - `sample_data/series.m3u`
- [ ] Confirm counts:
  - 5 channels
  - 3 movies
  - 2 series
  - 4 episodes
  - 12 source mappings
- [ ] Run `docker compose down`.
- [ ] Run `docker compose up -d`.
- [ ] Confirm catalog counts remain.
- [ ] Re-import the same sample playlists.
- [ ] Confirm no duplicate catalog items are created.
- [ ] Clear test data from the Catalog page or `POST /api/catalog/clear-test-data`.
- [ ] Re-import successfully.
- [ ] Confirm no broker, STRM, HDHomeRun, IPTV parsing beyond M3U metadata import, or media integrations are present.

## Sprint 3 Provider And Availability Acceptance Test

- [ ] Start Docker with `docker compose up -d`.
- [ ] Create two Providers.
- [ ] Add multiple Accounts / Connections under each Provider.
- [ ] Edit one Account / Connection and verify it updates in place without exposing the stored password.
- [ ] Verify the Account form does not require or own a playlist URL.
- [ ] Verify Account create/edit shows validation errors for missing provider or account name.
- [ ] Import using a playlist path/URL plus Provider and Account selection.
- [ ] Verify Catalog Import shows validation errors for missing provider, missing account, missing playlist, invalid local path, unreadable file, and unsupported media type.
- [ ] Verify failed imports do not change provider/account records and do not require recreating accounts.
- [ ] Verify Reset/New clears forms and exits edit mode.
- [ ] Import `sample_data/live.m3u` and verify it imports more than 0 entries.
- [ ] Import `/iptvboss/outputs/THREAD1.m3u` when that container path exists and verify it imports more than 0 entries.
- [ ] Verify a large playlist import does not exhaust memory.
- [ ] Verify Catalog tables remain responsive after a massive import.
- [ ] Verify catalog and source API endpoints return paginated results.
- [ ] Import `sample_data/live.m3u` under Account 1.
- [ ] Import the same live playlist under Account 2.
- [ ] Verify catalog item count does not double.
- [ ] Verify source availability count increases.
- [ ] Open Catalog > Sources and verify one catalog item can show multiple sources.
- [ ] Disable one account.
- [ ] Verify the catalog item remains while account/source status changes.
- [ ] Run `docker compose down`.
- [ ] Run `docker compose up -d`.
- [ ] Verify providers, accounts, and sources persist.
- [ ] Re-import the same playlist under the same account.
- [ ] Verify source availability records update, not duplicate.
- [ ] Verify importing a matched playlist under another account adds source records, not duplicate catalog items.
- [ ] Run a connection test from Accounts.
- [ ] Verify health status updates.
- [ ] Confirm no stream playback, broker routing, failover, STRM generation, HDHomeRun output, or integrations exist yet.

## Sprint 4 Broker Decision Acceptance Test

- [ ] Create Account A and Account B with max streams set to `1`.
- [ ] Import the same catalog item under both accounts so it has two source availability records.
- [ ] Resolve the item once from Broker and verify Account A is selected.
- [ ] Resolve the item again before releasing and verify Account B is selected.
- [ ] Resolve a third time and verify the Broker returns no available source because all eligible accounts are at capacity.
- [ ] Release the first reservation.
- [ ] Resolve again and verify Account A can be selected again.
- [ ] Resolve a movie on Account A, then resolve a live TV channel, and verify Account A is treated as busy across media types.
- [ ] Confirm reservations expire automatically after the configured TTL and no longer count against account capacity.
- [ ] Confirm no playback, proxy streaming, transcoding, STRM generation, HDHomeRun output, or media-server integration is present.

## Sprint 4.1 Broker Polish Acceptance Test

- [ ] Resolve with multiple matching accounts and verify the highest-weight available account is selected.
- [ ] Resolve again and verify the next account is selected when the first account is at capacity.
- [ ] Resolve a third time and verify a friendly all-at-capacity message appears.
- [ ] Confirm the Broker UI never shows `[object Object]`.
- [ ] Confirm evaluated candidates show selected/skipped state, account, provider, priority, weight, usage, health, and reason.
- [ ] Release one reservation and verify account availability updates.
- [ ] Use Release All Active and verify active reservations clear.
- [ ] Use Expire Now and verify status cards refresh immediately.

## Sprint 5 Source Resolution Runtime Acceptance Test

- [ ] Pick a movie catalog item with source availability on Account A and Account B.
- [ ] Call `/r/movie/{id}?debug=true` and verify the highest-weight available account is selected.
- [ ] Call the same debug URL again before reservation expiry and verify the next available account is selected.
- [ ] Call it a third time and verify a clear `all_at_capacity` error is returned.
- [ ] Release or expire reservations and verify runtime resolve works again.
- [ ] Call `/r/movie/{id}` without debug and verify it returns HTTP `302`.
- [ ] Confirm debug mode returns JSON and does not redirect.
- [ ] Confirm Catalog and Broker UI pages show stable Media Router runtime URLs.
- [ ] Set Runtime Public Base URL to `http://localhost:8088` and confirm Catalog/Broker previews use it.
- [ ] Confirm no STRM files are generated in Sprint 5.

## Sprint 6 STRM Output Acceptance Test

- [ ] Configure Movies output path and Series output path using container paths.
- [ ] Validate paths and confirm Movies and Series output directories are writable.
- [ ] Run STRM dry-run and confirm no files are written.
- [ ] Run Generate and confirm movie and episode `.strm` files are created.
- [ ] Open a generated `.strm` file and confirm it contains a Media Router runtime URL.
- [ ] Confirm no provider URL or credentials appear in generated files.
- [ ] Re-run Generate and confirm unchanged files are skipped.
- [ ] Change Runtime Public Base URL and re-run Generate; confirm files update.
- [ ] Run dry-run with orphan cleanup enabled and confirm orphan cleanup preview only includes tracked generated files.
- [ ] Make an output path invalid or read-only and confirm Generate fails gracefully with the path and reason.
- [ ] Restart Docker and confirm STRM settings and generated file history persist.

## Sprint 7 Live TV M3U Output Acceptance Test

- [ ] Configure Live M3U output path, for example `/outputs/live/live.m3u`.
- [ ] Validate output path and confirm the parent directory is writable.
- [ ] Run Live M3U dry-run and confirm no file is written.
- [ ] Confirm the preview contains `/r/live/{catalog_item_id}` runtime URLs only.
- [ ] Run Generate and confirm the M3U file is created.
- [ ] Open the generated M3U file and confirm no provider URLs or credentials appear.
- [ ] Confirm `#EXTINF` includes `tvg-chno` when source data has it.
- [ ] Confirm `group-title` is preserved.
- [ ] Confirm channel ordering follows channel number, group, then title.
- [ ] Confirm runtime live resolve defaults to a long live-TV TTL.
- [ ] Confirm `ttl` query parameter still overrides the runtime TTL.
- [ ] Confirm Recent Generated Files appears at the bottom of the Outputs page.
- [ ] Open one generated channel URL in VLC and confirm playback routes through Media Router.
- [ ] Resolve the same channel multiple times and confirm Broker capacity behavior still works.
- [ ] Restart Docker and confirm Live M3U settings and run history persist.
