# Media Router

Media Router is a provider-agnostic media routing platform for home media systems. It centralizes catalog identity, provider/account availability, capacity-aware source selection, stable runtime URLs, and disposable client outputs for Live TV, movies, and series.

The current `v0.8.1` build is a working platform under production validation. It is successfully brokering playback for Emby and Channels DVR, and its generated outputs have also been exercised with Jellyfin, VLC, and Kodi.

Media Router resolves playback requests and returns the selected provider source with an HTTP `302` redirect. It does **not** currently proxy or transcode media streams.

## Current architecture

```text
Provider playlists
      ↓
Catalog identity
      ↓
Source availability
      ↓
Broker and reservations
      ↓
Stable runtime URLs
      ↓
STRM and Live M3U outputs
      ↓
Emby / Channels DVR / Jellyfin / Kodi / VLC
```

## What exists now

- FastAPI application and Docker Compose deployment.
- SQLite-backed catalog, providers, accounts, source availability, reservations, generated-file tracking, and Live channel placements.
- Stable runtime routes:
  - `/r/live/{id}`
  - `/r/movie/{id}`
  - `/r/episode/{id}`
- Capacity-aware Broker with priority, weight, enablement, health metadata, reservation status, manual release, and live polling UI.
- Atomic provisional reservation acquisition, evidence-based active promotion, and identity/alias reuse for probes, seeks, range requests, reconnects, and slow provider startup.
- Atomic same-session Live channel switching, provisional VOD browsing supersession, sliding active renewal, and explicit lifecycle APIs.
- Conservative Emby startup coalescing when probe and playback requests use different User-Agent families.
- Movie and episode STRM generation with dry-run, configurable limits, batched processing, bounded file concurrency, tracking, cleanup, cancellation, and benchmark logging.
- Live TV M3U generation with channel-number, group, metadata, source-order, and repeated editorial-placement preservation.
- Paginated APIs and UI tables for large catalogs.
- Persistent wizard, settings, jobs, logs, and operational metadata.

## Current limitations

- Redirect mode cannot observe byte-level disconnects; explicit lifecycle APIs are available, while media-server event integrations and optional proxy observation remain future work.
- XMLTV is currently supplied externally, such as from IPTV Boss or another web server.
- Generated outputs are files on disk; native HTTP-served M3U and XMLTV endpoints are planned for v1.0.
- Proxy streaming, transcoding, HDHomeRun emulation, encrypted secret storage, and formal media-server adapters are not yet implemented.
- Kodi IPTV Simple may apply its own channel ordering or duplicate-placement behavior even when using the original IPTV Boss playlist. A Kodi-specific output profile is a post-1.0 compatibility idea, not a core-output blocker.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8088
```

Open `http://localhost:8088`.

## Run with Docker

```bash
docker compose up --build -d
```

Persistent application data is stored under `./data` and mounted into the container as `/data`.

To force current Git metadata into the build:

```bash
MEDIA_ROUTER_GIT_BRANCH="$(git branch --show-current)" \
MEDIA_ROUTER_GIT_COMMIT="$(git rev-parse --short HEAD)" \
docker compose up --build -d
```

## Output storage guidance

STRM generation creates a large number of very small files. When Media Router and the consuming media server run on the same host, store STRM outputs on local storage and mount the same host directories into both containers.

Example:

```yaml
services:
  media-router:
    volumes:
      - /opt/mediarouter/outputs:/outputs

  emby:
    volumes:
      - /opt/mediarouter/outputs/movies:/media-router/movies:ro
      - /opt/mediarouter/outputs/series:/media-router/series:ro
```

Network shares can become metadata and round-trip bottlenecks when generating or scanning hundreds of thousands of tiny files.

The output paths configured in the UI are container paths, for example:

```text
/outputs/movies
/outputs/series
/outputs/live/live.m3u
```

Use **Outputs → Validate Paths** before generation.

## Catalog counts

Catalog identity and source availability are different measurements:

- Catalog items are unique channels, movies, series, and episodes.
- Source availability rows represent the provider/account sources that can satisfy those items.

With four equivalent accounts, one catalog item can have four source-availability rows. The dashboard should present these as separate counts; a card showing the larger source-row total as “Catalog” is a known clarity issue.

## Runtime reservations

Runtime reservation acquisition is atomic. A runtime GET starts a capacity-consuming provisional lease. Matching GET, HEAD, Range, reconnect, and startup requests reuse the same reservation instead of consuming additional capacity. Continued meaningful activity after the configured minimum age promotes that same ID to active.

Media Router prefers an explicit `client_session`. Otherwise, it uses a privacy-safe derived fingerprint. A short, conservative startup-coalescing fallback can alias a changed Emby/ffmpeg fingerprint to exactly one recent same-origin reservation.

Defaults are 45 seconds provisional and four hours active for Live TV, 60 seconds provisional and three hours active for movies, and 60 seconds provisional and two hours active for episodes. Promotion defaults to a 20-second minimum age and two meaningful requests. Sliding active renewal and safe same-identity supersession are enabled. Runtime `ttl=` overrides active TTL only; manual Broker tests retain short active diagnostic leases.

## Validated clients

- Emby: Live TV and STRM movie playback validated.
- Channels DVR: generated Live TV M3U ingestion and playback validated.
- Jellyfin: runtime STRM playback exercised.
- VLC: direct runtime playback exercised.
- Kodi IPTV Simple: playback works, but Kodi may apply client-specific channel ordering or placement behavior.

## Road to v1.0

The current focus is production readiness and the remaining Core v1.0 capabilities:

- Runtime URL configuration polish.
- HTTP-served Live M3U.
- XMLTV strategy and HTTP-served XMLTV.
- Provider health monitoring.
- Reservation lifecycle operational validation and client-integration follow-up.
- Backup and restore guidance/tooling.
- Setup wizard refinements.
- Operational diagnostics and upgrade safety.

HDHomeRun emulation, local-media providers, Kodi-specific output profiles, and richer media-server adapters are planned as post-1.0 ecosystem work.

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
- [Changelog](CHANGELOG.md)
