# Decisions

Architecture decisions should be recorded as ADRs in `docs/adr/` when they require full context and consequences.

## Accepted decisions

| Decision | Status | Notes |
| --- | --- | --- |
| Foundation first | Accepted | Begin with a minimal runnable foundation and clear module boundaries. |
| Python 3 and FastAPI | Accepted | Matches the target platform and provides clear API contracts. |
| Docker Compose deployment | Accepted | Fits the target Ubuntu home-server environment. |
| SQLite first | Accepted | Appropriate for a single-server home deployment; migration and backup hardening remain active work. |
| Module-first architecture | Accepted | Catalog, providers, Broker, runtime, outputs, and integrations remain separate. |
| UI-first configuration | Accepted | Normal operation should not require editing application YAML. |
| IPTV Boss as editorial input | Accepted | Media Router reads exports but does not edit IPTV Boss-managed data. |
| One canonical catalog identity | Accepted | Each channel, movie, series, and episode has one internal identity independent of provider accounts. |
| Source availability separate from identity | Accepted | Provider/account source rows do not multiply catalog identities. |
| Provider-agnostic availability | Accepted | Providers and accounts are separate from catalog identity and output presentation. |
| Runtime URLs before outputs | Accepted | Clients consume stable Media Router URLs; providers remain hidden behind the Broker. |
| Disposable outputs | Accepted | STRM and M3U files are rebuildable artifacts, not authoritative state. |
| STRM outputs use runtime URLs | Accepted | Generated movie and episode STRM files never contain provider credentials or direct provider URLs. |
| Live M3U uses runtime URLs | Accepted | Generated Live M3U entries route through `/r/live/{catalog_item_id}`. |
| Decision-and-redirect runtime | Accepted | Media Router brokers and redirects playback but does not currently proxy or transcode media. |
| Three-state reservation leases | Accepted | Runtime requests acquire short provisional capacity, sustained or explicit evidence promotes the same ID to active, and released/expired/superseded rows retain audit history. |
| Active-lifetime identity reuse | Accepted | Matching probes, GET, HEAD, Range, seek, and reconnect requests reuse one reservation. |
| Atomic reservation acquisition | Accepted | SQLite locking and active-playback uniqueness prevent concurrent duplicate reservations. |
| Conservative content supersession | Accepted | Same-session Live switches atomically replace one consuming lease; movie/episode switches replace only a prior provisional VOD lease and never active VOD playback. |
| Emby polling as lifecycle evidence | Accepted | A MediaRouter-side poller observes Emby without requiring a plugin and calls the existing Broker transition services. Failed or uncertain polls fail open and never imply stopped playback. |
| Conservative startup coalescing | Accepted | A changed derived fingerprint may alias to exactly one recent same-origin reservation; ambiguous or conflicting sessions never coalesce. |
| Trusted proxy headers are opt-in | Accepted | Forwarded client headers are ignored unless the proxy and header source are explicitly trusted. |
| Editorial placement separate from channel identity | Accepted | Repeated playlist memberships preserve group, number, metadata, and order while sharing one runtime identity. |
| Bounded output generation | Accepted | Catalog reads, filesystem workers, UI previews, and database commits remain bounded for large catalogs. |
| Local STRM storage preferred on same host | Accepted | When Media Router and the media server share a host, generate STRM files on local storage and mount them into both containers. Network shares can become tiny-file metadata bottlenecks. |
| Client DVR ownership | Accepted | Emby, Channels DVR, Jellyfin, Kodi, and similar front ends retain DVR, recording, history, and playback presentation responsibilities. |
| NextPVR is not required in the core path | Accepted | Media Router can replace NextPVR's playlist aggregation/distribution role when front ends already provide DVR capabilities. |
| Client-specific output profiles are optional | Accepted | The generic output remains standards-oriented; Kodi-specific metadata behavior may be handled later by a compatibility profile. |
| Native HTTP outputs belong in Core v1.0 | Accepted | Live M3U and XMLTV should ultimately be served directly by Media Router, replacing the temporary static file server. |
| HDHomeRun emulation is post-1.0 | Accepted | It is useful ecosystem work but not required for the Core v1.0 release. |

## Product boundary

Media Router owns:

- Catalog identity.
- Provider/account source availability.
- Account capacity and Broker decisions.
- Stable runtime URLs.
- Disposable output generation and distribution.

Client applications own:

- Playback UI.
- DVR and recording behavior.
- Viewing history.
- Transcoding and playback presentation.
- Client-specific channel and guide presentation.

Media Router is middleware, not another full media server.

## Current operational decisions

### Development and production separation

- Code changes are developed and tested in the development checkout.
- Production deployment pulls committed changes and rebuilds Docker images.
- Server-specific Compose overrides may remain local when they contain host paths or temporary services.

### Output storage

- Large STRM libraries should be generated on local storage when possible.
- The consuming media-server container should receive the same host directories as read-only mounts.
- Network output remains supported but is not the recommended high-scale deployment model.

### Client validation

- Emby Live TV and STRM movie playback are validated.
- The 2026-08-31 isolated Mac mini Stage 5A test validated movie capacity enforcement with the exact VOD mapping and a synthetic account limited to one stream. The first Emby Web session adopted and promoted its provisional reservation, remained heartbeating at capacity 1/1, and released normally after the configured disappearance grace period. A distinct Safari Emby session was refused with Emby's no-compatible-streams playback error; a contemporaneous distinct Emby-shaped runtime request returned HTTP 409 `all_at_capacity`. No second reservation, binding, or fixture request appeared, and capacity never exceeded 1/1. The test used only the lab server and a 300-second synthetic fixture.
- The 2026-07-28 production deployment of application commit `8b97d0c` validated the Emby provisional-reservation adoption path end to end: automatically and manually mapped sessions adopted and promoted the original provisional reservation, no duplicate capacity-consuming reservation remained, heartbeats continued, playback-end release completed, and unrelated provisional behavior remained functional.
- Current Emby release timing remains unchanged. Media-type-aware abandoned-session cleanup may be optimized only in a later lifecycle phase after polling support and validation cover live channels, movies, and series episodes; that work is separate from the full-library mapping audit.
- The 2026-07-28 production validation of the preview-only Emby mapping audit approved the endpoint for operator-driven, read-only audits. Operators must request one media type at a time, prefer lower-load periods for movie and episode scans, and treat `truncated=true` results as partial. The endpoint is not approved for frequent polling, unattended automation, or applying VOD mappings.
- Channels DVR Live TV ingestion and playback are validated.
- Jellyfin and VLC runtime playback have been exercised.
- Kodi IPTV Simple playback works, but Kodi may apply its own channel ordering or duplicate-placement behavior. The same behavior with the original IPTV Boss playlist indicates a client-specific presentation issue rather than a core Media Router output defect.

### Emby mapping-audit production validation

Application commit `b52f2fc` was validated on `embyserver` with one request per media
type. The API remained ready, the container identity and start time did not change,
no application errors occurred, and mappings, reservations, Emby bindings, playback,
and lifecycle state were unchanged. Returned diagnostics contained no sensitive URLs,
paths, tokens, credentials, or raw Emby DTOs.

| Media type | Scan result | Classification summary | Evidence and diagnostics | Observed cost |
| --- | --- | --- | --- | --- |
| Channel | 2,479; complete | 2,151 exact; 328 ambiguous | 2,151 persisted ItemId; 328 duplicate Emby names; 80 duplicate catalog titles; 80 placement collisions | Previously validated successfully |
| Movie | 10,000; truncated | 6,795 exact; 3,205 ambiguous | 6,782 durable markers; 13 catalog external identities; 3,120 duplicate Emby names; 448 duplicate catalog titles; 3,197 conflicting exact evidence | 41.424 s; about 84% peak CPU; 177.5 MiB peak memory |
| Series | 16; complete | 8 normalized-title; 8 unmatched | No ambiguity or collisions | 0.587 s |
| Episode | 1,596; complete | 848 exact structural identity; 748 unmatched | 4 incomplete structures; 20 duplicate Emby names; 209 duplicate catalog titles; no duplicate structural identities | 5.362 s; about 101% peak CPU; 721.5 MiB peak memory, returning immediately to normal |

Movie aggregates describe only the first 10,000 scanned items and must not be
interpreted as full-library totals. Integrity hashes used around an audit must order
rows by a complete stable key; an incomplete `ORDER BY` produced incomparable hashes
during episode validation. The approved procedure is documented in the
[Emby mapping-audit runbook](EmbyAuditRunbook.md).

### Catalog metrics

- Unique catalog items and source-availability rows are separate metrics.
- Multiple provider accounts can produce several source rows for one canonical item.
- Dashboard labels must not present source-row totals as unique catalog size.

## Open decisions

- Migration framework and versioning policy.
- Backup/restore implementation depth for v1.0.
- Whether local-network UI authentication is enabled by default or optional.
- Provider health-scoring policy.
- Default reservation TTLs and startup-coalescing windows by media type.
- XMLTV ingestion versus generation responsibilities.
- Whether output plugins can be installed dynamically or remain bundled.
- How catalog duplicate detection should score uncertain matches.
- Whether heartbeat/explicit stop integration belongs in v1.x core or client adapters.

## ADR template

```markdown
# ADR NNNN: Title

## Status

Proposed | Accepted | Superseded

## Context

What problem or tradeoff are we addressing?

## Decision

What did we decide?

## Consequences

What becomes easier, harder, or constrained?
```
