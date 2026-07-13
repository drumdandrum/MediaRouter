# Installation

## Current Foundation Install

The foundation app is a minimal FastAPI service with a static architecture page and two API endpoints.

## How To Run Locally

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the app:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8088
```

Open:

```text
http://localhost:8088
```

For development on the same machine:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8088
```

## Docker

Build and run:

```bash
docker compose up --build
```

The app version is `v0.8.1`. Docker receives version and Git metadata through build/runtime environment values:

```text
MEDIA_ROUTER_APP_VERSION=v0.8.1
MEDIA_ROUTER_GIT_BRANCH=main
MEDIA_ROUTER_GIT_COMMIT=<short commit>
```

To rebuild with the current local Git metadata:

```bash
MEDIA_ROUTER_GIT_BRANCH="$(git branch --show-current)" \
MEDIA_ROUTER_GIT_COMMIT="$(git rev-parse --short HEAD)" \
docker compose up --build -d
```

The foundation compose file mounts persistent application data:

```yaml
volumes:
  - ./data:/data
```

The application runs with:

```text
MEDIA_ROUTER_DATA_DIR=/data
```

Settings, wizard state, and job history are stored in `/data` inside the container and `./data` on the host.

The compose file should mount output folders for implemented output modules as read-write container paths. IPTVBoss exports may be mounted read-only for imports.

## Future Production Shape

Expected future media-related Docker mounts:

```yaml
volumes:
  - ./data:/data
  - /Users/Shared/IPTVBoss/output:/iptvboss/output:ro
  - /Users/Shared/MediaRouter/strm/movies:/outputs/movies
  - /Users/Shared/MediaRouter/strm/series:/outputs/series
  - /Users/Shared/MediaRouter/live:/outputs/live
  - /var/run/docker.sock:/var/run/docker.sock:ro
  - /mnt:/mnt:ro
  - /media:/media:ro
  - /srv:/srv:ro
```

Expected future environment variables:

```text
MEDIA_ROUTER_APP_PORT=8088
MEDIA_ROUTER_PUBLIC_BASE_URL=http://localhost:8088
MEDIA_ROUTER_DATABASE_URL=sqlite:////data/media-router.db
```

For Sprint 5 runtime URL previews, the preferred user-facing setting is Settings > Runtime > Runtime Public Base URL. Set it to a browser/client-visible address such as `http://localhost:8088` for local testing. If that field is blank, Media Router uses `MEDIA_ROUTER_PUBLIC_BASE_URL` when it is not the Docker-internal `media-router` hostname, then falls back to the current request host/scheme.

For Sprint 6 STRM output, configure output directories as container paths such as `/outputs/movies` and `/outputs/series`. Mount host folders into those paths with Docker as read-write volumes. Do not enter Mac host paths in the Media Router UI. IPTVBoss imports can be mounted read-only, for example `/Users/Shared/IPTVBoss/output:/iptvboss/output:ro`.

For Sprint 7 Live TV M3U output, configure the output file as a container path such as `/outputs/live/live.m3u`. Mount the parent host folder as read-write:

```yaml
volumes:
  - /Users/Shared/MediaRouter/live:/outputs/live
```

Generated Live TV M3U playlists are disposable. They contain Media Router runtime URLs such as `http://localhost:8088/r/live/channel_abc123`, not direct provider URLs or credentials.

## STRM Generation Sizing

For an 8 GB Mac mini development system, use Test mode (500 movies/500 episodes) and batch size 250. Small mode is reasonable for controlled validation when other memory-heavy services are quiet.

For a larger Linux server, start with Medium mode (5,000 movies/10,000 episodes) and batch size 500. Use Custom to raise one media limit deliberately. Select Unlimited only after a dry run, checking estimated counts and free output-disk capacity; the UI requires a separate confirmation before generation.

Settings and job history remain under the `/data` mount, so Docker restarts preserve the selected mode, limits, batch size, progress history, and run summaries. Cancelling an active STRM job stops after its current batch and preserves completed files/tracking records.

For Live M3U on an 8 GB Mac mini development system, use Test mode at 500 channels. For the Linux `embyserver`, deploy in stages: first validate Test mode and playback, then try Small or Medium, then choose a measured Custom limit. Use Unlimited only after a successful dry run confirms eligible counts and output-disk capacity; Generate requires a separate confirmation. Live settings persist in `/data/outputs_live_m3u_settings.json` across Docker restarts.

## Verification

Health check:

```bash
curl http://localhost:8088/api/health
```

Expected response:

```json
{"status":"ready"}
```

Foundation metadata:

```bash
curl http://localhost:8088/api/foundation
```

Persistence check:

```bash
docker compose up -d
# Save settings in the UI, complete a wizard step, and run a test job.
docker compose down
docker compose up -d
# Re-open the UI and verify settings, wizard state, and completed job history remain.
```

## Upgrade Notes

Until database migrations exist, upgrades are code-only. Once persistence is added, every release that changes schema must include migration instructions.
