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
- Channels DVR Live TV ingestion and playback are validated.
- Jellyfin and VLC runtime playback have been exercised.
- Kodi IPTV Simple playback works, but Kodi may apply its own channel ordering or duplicate-placement behavior. The same behavior with the original IPTV Boss playlist indicates a client-specific presentation issue rather than a core Media Router output defect.

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
