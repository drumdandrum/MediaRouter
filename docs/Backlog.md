# Backlog

## Epic 1

Foundation

### Sprint 1

☑ Docker
☑ FastAPI
☑ Wizard
☑ Settings
☑ Dashboard
☑ Job System
☑ Principles checklist
☑ Documentation index

### Sprint 1.5

☑ Step-by-step wizard polish
☑ Manual placeholder values
☑ Categorized settings
☑ Host path and container path fields
☑ Friendly dashboard status badges
☑ About/System page
☑ Test job lifecycle
☑ Logs page with secret scrubbing
☑ README run instructions
☑ Sprint 1 acceptance checklist
☑ Docker data persistence
☑ Sidebar version display
☑ About/System metadata cleanup
☑ Docker/container detection

## Epic 2

Catalog

### Sprint 2

☑ M3U Parser
☐ XMLTV Parser
☑ Catalog Database
☑ Internal ID strategy
☑ Duplicate detection
☑ Source URL mapping
☑ Catalog import jobs
☑ Catalog UI
☑ Sample data
☐ Existing STRM scanner

## Epic 3

Provider And Availability

### Sprint 3

☑ Provider CRUD
☑ Provider types
☑ Account CRUD
☑ Redacted account reads
☑ Priority groups
☑ Account weights
☑ Lightweight connection test
☑ Source availability records
☑ Catalog import provider/account assignment
☑ Dashboard availability summary
☑ Account playlist URL removed
☑ Streaming M3U import
☑ Catalog/source pagination
☐ Secret encryption

## Epic 4

Broker

### Sprint 4

☑ Reservation model
☑ Resolve endpoint
☑ Release endpoint
☑ Reservation expiry
☑ Account selection policy
☑ Shared account capacity tracking
☑ Broker status UI
☑ Broker page polling updates
☑ Broker explainability
☑ Evaluated candidate diagnostics
☑ Release All Active
☑ Broker URL builder

## Epic 5

Source Resolution Runtime

### Sprint 5

☑ Runtime resolve routes
☑ Runtime redirect mode
☑ Runtime JSON/debug mode
☑ Runtime HEAD redirect probes
☑ Runtime repeated GET reservation reuse
☑ Stable port-independent runtime fingerprints
☑ Full active-lifetime GET/HEAD/Range reservation reuse
☑ Atomic SQLite reservation reuse-or-create
☑ Duplicate reservation diagnostics and repair action
☑ Reservation TTL query parameter
☑ Runtime client_session query parameter
☑ Client label query parameter
☑ Runtime preview API
☑ Catalog runtime URL preview
☑ Broker runtime URL preview
☑ Runtime Public Base URL setting
☑ Runtime URL request-host fallback
☑ Runtime error details
☑ STRM generation deferred

## Epic 6

Outputs

### Sprint 6

☑ STRM Output
☑ STRM settings persistence
☑ STRM dry-run
☑ STRM generation job
☑ Generated STRM file tracking
☑ STRM Outputs UI
☑ STRM output path validation
☑ STRM generation diagnostics
☑ Configurable STRM generation presets and custom limits
☑ Batched STRM catalog processing and incremental tracking commits
☑ STRM batch progress and cooperative cancellation
☑ STRM batch timing instrumentation and throughput benchmark metrics
☑ Bounded concurrent atomic STRM writes and bulk tracking upserts
☑ Runtime fingerprint input diagnostics and trusted proxy peer allowlist
☑ Short-window Emby startup identity coalescing and reservation aliases
☑ Paginated recent generated files
☐ XMLTV Output
☐ HDHomeRun Output
☐ Output plugin registry
☑ Build status
☑ Rebuild jobs
☑ Disposable output validation

### Sprint 7

☑ Live TV M3U Output
☑ Live M3U settings persistence
☑ Live M3U path validation
☑ Live M3U dry-run preview
☑ Live M3U generation job
☑ Live M3U history
☑ Live M3U preview API
☑ Live M3U Outputs UI
☑ Runtime URL-only generated playlists
☑ Live M3U `tvg-chno` preservation
☑ Live M3U channel sorting
☑ Configurable Live M3U Test/Small/Medium/Unlimited/Custom limits
☑ Live M3U eligibility estimates and excluded-by-limit reporting
☑ Paginated Live channel selection and streamed playlist output
☑ Explicit Unlimited Live M3U confirmation
☑ Separate Live channel editorial placement model
☑ Preserve repeated CUID memberships, group numbers, metadata, and source order
☑ Idempotent placement re-import with stale-position deactivation
☑ Placement-based Live M3U generation with shared runtime identity
☑ Live channel placement detail API/UI
☑ Runtime playback TTL defaults
☐ Client heartbeat / playback-end release
☐ Runtime proxy mode for media-server compatibility
☐ WebSockets / Server-Sent Events for live Broker updates

## Epic 7

Integrations

### Future Sprint

☐ IPTV Boss import watcher
☐ Emby adapter
☐ Jellyfin adapter
☐ NextPVR adapter
☐ Channels DVR validation
☐ Guide data handoff
☐ Library sync hooks

## Epic 8

Operations

### Sprint 8

☐ Logging
☐ Secret scrubbing
☐ Backup and restore
☐ Database migrations
☐ Health diagnostics
☐ UI authentication option
☐ Docker volume guidance
☐ Upgrade notes

## Epic 8

Plugin SDK

### Sprint 8

☐ Plugin metadata schema
☐ Service-layer plugin context
☐ Plugin status contract
☐ Plugin build contract
☐ Plugin settings schema
☐ Plugin permission rules
☐ Plugin test harness
