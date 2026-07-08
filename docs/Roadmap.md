# Roadmap

## Phase 0: Foundation

Deliverables:

- Minimal FastAPI app.
- Docker scaffold.
- Architecture docs.
- Module boundaries.
- Domain contracts.
- Health and foundation metadata API.

Exit criteria:

- Documentation is accepted.
- Module ownership is clear.
- Next implementation phase is selected.

## Phase 1: Settings And Wizard Shell

Deliverables:

- JSON-backed settings persistence.
- First-run wizard shell.
- Dashboard summary.
- Lightweight background job system.
- Principles visible in implementation checklist.
- Setup state tracking.

Exit criteria:

- User can complete a non-destructive wizard flow.
- Dashboard reports health, setup progress, settings count, and job counts.
- Foundation jobs can run asynchronously and report progress.
- Catalog and broker remain unimplemented.

## Phase 1.5: Foundation Polish

Deliverables:

- Real step-by-step first-run wizard: Welcome, Environment, Paths, Services, Review, Complete.
- Manual placeholder values for environment, paths, and services.
- Categorized settings: General, Storage, Network, Services, Advanced.
- Host path and container path fields.
- Friendly dashboard status badges.
- About/System page.
- Test job lifecycle for queued, running, and completed states.
- Logs page with secret scrubbing.
- README local run instructions and Sprint 1 acceptance checklist.
- Docker Compose persistent data mount for settings, wizard state, and job history.
- Version/About cleanup with app version `v0.2.1`, Git metadata, Docker detection, and container detection.

Exit criteria:

- Wizard completion persists after restart.
- Settings persist after restart.
- Dashboard uses user-friendly labels.
- Test job proves job state transitions before catalog jobs exist.
- Settings, wizard completion state, and completed job history persist through Docker down/up.
- Sidebar shows `v0.2.1`.
- About/System shows version, environment, branch, commit, database, Docker, and container status.
- No catalog, broker, STRM, HDHomeRun, IPTV parsing, or media integrations are implemented.

## Phase 2: Catalog Engine

Deliverables:

- SQLite-backed catalog database under `/data`.
- M3U parser for live, movie, and series playlists.
- Stable internal IDs for channels, movies, series, and episodes.
- Separate source mapping table for future multi-source/account support.
- Series/episode naming parser with confidence flag.
- Catalog import jobs.
- Catalog UI page with Overview, Live Channels, Movies, Series, Episodes, and Sources.
- Sample playlists under `sample_data/`.

Exit criteria:

- Sample import produces 5 channels, 3 movies, 2 series, 4 episodes, and 12 source mappings.
- Docker restart preserves catalog data.
- Re-importing sample playlists does not create duplicate catalog items.
- Catalog test data can be cleared and re-imported.
- No broker, STRM, HDHomeRun, IPTV parsing beyond M3U metadata import, or integrations are implemented.

## Phase 3: Provider, Account, And Source Availability

Current phase.

Deliverables:

- Provider create/read/update/delete.
- Provider-agnostic provider types.
- Account/connection create/read/update/delete.
- Redacted account read models.
- Local secret field with future encryption task.
- Provider connection test interface.
- Account health metadata.
- Source availability records that connect catalog items to provider/accounts.
- Catalog import assignment to provider/account.
- Streaming M3U import with batched writes.
- Paginated catalog/source APIs and bounded UI tables.
- Dashboard provider/account/source availability summary.

Exit criteria:

- Providers and accounts can be configured through the UI.
- Credentials are not exposed in logs or reads.
- Importing the same playlist under two accounts keeps one catalog identity and creates multiple availability records.
- Re-importing the same playlist under the same account updates sources instead of duplicating them.
- Accounts do not own playlist URLs; Catalog Import associates playlist paths/URLs to provider/accounts.
- Docker restart preserves providers, accounts, and source availability.
- No broker routing, failover, stream playback, STRM generation, HDHomeRun output, or integrations are implemented.

## Phase 4: Broker

Deliverables:

- Account selection policy.
- Active stream reservation model.
- Capacity-aware routing.
- Failover attempt sequence.
- Stable stream endpoints.

Exit criteria:

- Broker can route requests through selected provider accounts.
- Active stream usage is visible.

## Phase 5: Outputs

Deliverables:

- STRM output plugin.
- M3U output plugin.
- XMLTV output plugin.
- HDHomeRun output plugin.
- Output build/status jobs.

Exit criteria:

- Outputs are generated from catalog and broker contracts.
- STRM output preserves folder layout when rewriting existing libraries.
- Generated outputs can be deleted and rebuilt from catalog/settings.

## Phase 6: Integrations

Deliverables:

- Emby adapter.
- Jellyfin adapter.
- NextPVR adapter.
- Channels DVR validation.
- IPTV Boss folder watcher.

Exit criteria:

- Media servers can consume broker-backed outputs.
- IPTV Boss exports can trigger safe catalog updates.

## Phase 7: Hardening

Deliverables:

- Migration tests.
- Backup/restore guidance.
- Structured logs with secret scrubbing.
- Authentication option for the web UI.
- Operational diagnostics.

Exit criteria:

- The application is safe to run continuously on the home media server.
