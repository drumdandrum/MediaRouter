# Media Router

Media Router is currently a foundation, catalog engine, provider/account availability model, and Sprint 4 broker decision engine for a future Dockerized home media orchestration platform. The project intentionally does not implement stream playback, proxy streaming, transcoding, STRM rewriting, HDHomeRun output, or DVR/media-server integration yet.

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
MEDIA_ROUTER_APP_VERSION=v0.2.1
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
- App version `v0.2.1`.
- Sprint 4 web console.
- `/api/health` endpoint.
- `/api/foundation` endpoint with module metadata.
- Dashboard, wizard, settings, and jobs APIs.
- Catalog summary/list/import APIs.
- Provider and account/connection APIs.
- Source availability APIs.
- Broker decision, reservation, release, and status APIs.
- Broker UI for account usage, reservation status, source decision testing, and evaluated candidate explanations.
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
- STRM import or generation.
- IPTV Boss watching.
- HDHomeRun compatibility.
- Emby/Jellyfin/NextPVR/Channels adapters.
- Encrypted secrets storage.
- Real stream playback.
- HDHomeRun output.
- Production dashboard UI.

Sprint 4 includes a decision-only Broker. It chooses the best source for a catalog item and creates a temporary account reservation. It does not play, proxy, transcode, or generate streams. Outputs, STRM, HDHomeRun, and media-server integrations are still deferred.

Sprint 4.1 polishes Broker diagnostics. Resolve results now show the selected account, provider, priority, weight, usage, TTL, selection reason, and every evaluated candidate with a selected/skipped reason.

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
