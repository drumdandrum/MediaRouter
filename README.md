# Media Router

Media Router is currently an architecture foundation for a future Dockerized home media orchestration platform. The project intentionally does not implement IPTV brokering, catalog import, STRM rewriting, or DVR integration yet.

The goal of this phase is to lock down module ownership, boundaries, and delivery order before building features.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8088
```

Open `http://localhost:8088`.

## Run With Docker

```bash
docker compose up --build
```

## What Exists Now

- Minimal FastAPI app.
- Sprint 1 web console.
- `/api/health` endpoint.
- `/api/foundation` endpoint with module metadata.
- Dashboard, wizard, settings, and jobs APIs.
- JSON-backed settings and wizard state.
- In-memory foundation job system.
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

- IPTV account CRUD.
- Broker routing.
- STRM import or generation.
- IPTV Boss watching.
- HDHomeRun compatibility.
- Emby/Jellyfin/NextPVR/Channels adapters.
- SQLite schema.
- Secrets storage.
- Full dashboard UI.

Sprint 1 includes a dashboard foundation only; catalog, broker, accounts, outputs, and integrations are still deferred. Those should be added incrementally after the architecture decisions in `docs/Architecture.md` and `docs/Roadmap.md` are accepted.
