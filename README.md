# Media Router

Media Router is currently an architecture foundation for a future Dockerized home media orchestration platform. The project intentionally does not implement IPTV brokering, catalog import, STRM rewriting, or DVR integration yet.

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
- Sprint 1.5 web console.
- `/api/health` endpoint.
- `/api/foundation` endpoint with module metadata.
- Dashboard, wizard, settings, and jobs APIs.
- About/System and logs APIs.
- JSON-backed settings and wizard state.
- Persistent JSON-backed job history.
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
- Production dashboard UI.

Sprint 1.5 includes foundation polish only; catalog, broker, accounts, outputs, and integrations are still deferred. Those should be added incrementally after the architecture decisions in `docs/Architecture.md` and `docs/Roadmap.md` are accepted.

## Sprint 1 Acceptance Test

- [ ] App starts locally on port `8088`.
- [ ] Dashboard shows Ready/Needs setup/Not configured labels, not raw status codes.
- [ ] Wizard shows Welcome, Environment, Paths, Services, Review, Complete.
- [ ] Wizard accepts manual placeholder values.
- [ ] Wizard completion persists after restart.
- [ ] Settings are grouped into General, Storage, Network, Services, Advanced.
- [ ] Settings include host path and container path fields.
- [ ] Settings persist after restart.
- [ ] Test job shows queued, running, and completed states.
- [ ] Logs page formats sanitized log entries.
- [ ] About page shows version, environment, Git, database, Docker, and container status.
- [ ] No catalog, broker, STRM, HDHomeRun, IPTV parsing, or media integrations are present.

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
