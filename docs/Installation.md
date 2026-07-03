# Installation

## Current Foundation Install

The foundation app is a minimal FastAPI service with a static architecture page and two API endpoints.

## Local Development

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

## Docker

Build and run:

```bash
docker compose up --build
```

The foundation compose file intentionally does not mount media folders, Docker sockets, IPTV Boss exports, or persistent database storage yet.

Those mounts should be introduced only when their modules are implemented.

## Future Production Shape

Expected future Docker mounts:

```yaml
volumes:
  - ./data:/data
  - /var/run/docker.sock:/var/run/docker.sock:ro
  - /mnt:/mnt:ro
  - /media:/media:ro
  - /srv:/srv:ro
```

Expected future environment variables:

```text
MEDIA_ROUTER_APP_PORT=8088
MEDIA_ROUTER_PUBLIC_BASE_URL=http://media-router:8088
MEDIA_ROUTER_DATABASE_URL=sqlite:////data/media-router.db
```

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

## Upgrade Notes

Until database migrations exist, upgrades are code-only. Once persistence is added, every release that changes schema must include migration instructions.
