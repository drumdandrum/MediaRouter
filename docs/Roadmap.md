# Roadmap

## Phase 0: Foundation

Current phase.

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

## Phase 2: Accounts

Deliverables:

- IPTV account create/read/update/delete.
- Redacted account read models.
- Credential storage strategy.
- Provider connection test interface.
- Account health metadata.

Exit criteria:

- Accounts can be configured through the UI.
- Credentials are not exposed in logs or reads.

## Phase 3: Catalog

Deliverables:

- M3U parser.
- Existing STRM scanner.
- Stable internal ID strategy.
- Duplicate detection before ID creation.
- Catalog source mapping.
- Import preview and apply flow.

Exit criteria:

- Existing libraries can be scanned without changing files.
- Imported items receive stable IDs.
- One movie, episode, or live channel cannot be represented by duplicate internal IDs.

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
