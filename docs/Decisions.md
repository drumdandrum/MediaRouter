# Decisions

Architecture decisions should be recorded as ADRs in `docs/adr/`.

## Accepted Decisions

### 0001: Foundation First

Status: Accepted

Media Router starts with a minimal runnable foundation instead of a partial full implementation.

Reasons:

- The product vision is large.
- Premature implementation would blur module boundaries.
- Foundational contracts should be clear before writing broker/catalog/account logic.

See `docs/adr/0001-foundation-first.md`.

## Initial Technical Decisions

| Decision | Status | Notes |
| --- | --- | --- |
| Python 3 | Accepted | Matches requested platform direction. |
| FastAPI | Accepted | Clear API contracts and built-in OpenAPI documentation. |
| Docker Compose | Accepted | Fits the target Ubuntu Docker server. |
| SQLite first | Proposed | Good fit for single-server home deployment, pending persistence phase. |
| Module-first architecture | Accepted | Keeps broker, catalog, outputs, and integrations separate. |
| Plugin-shaped outputs | Accepted | STRM, M3U, XMLTV, HDHomeRun, and future outputs should share a contract. |
| UI-first configuration | Accepted | Normal users should not edit YAML. |
| IPTV Boss as editorial source | Accepted | Media Router reads exports but never edits IPTV Boss data. |
| Runtime catalog ownership | Accepted | Media Router owns runtime identity and source mappings. |
| Disposable outputs | Accepted | Outputs are generated artifacts, not authoritative state. |
| Service-layer plugin boundary | Accepted | Plugins never directly access SQLite. |
| One internal ID per item | Accepted | Each movie, episode, and live channel has exactly one internal ID. |
| SQLite catalog foundation | Accepted | Sprint 2 stores catalog identity and source mappings in `/data/media_router.db`. |
| Source mappings separate from identity | Accepted | Provider URLs live in `catalog_sources`, not as catalog identity. |
| Redacted source read models | Accepted | Sprint 2 stores source URLs for future routing but redacts credential path segments in API/UI reads. |
| Provider-agnostic availability | Accepted | Sprint 3 models providers and accounts separately from catalog identity. |
| Broker deferred through Sprint 3 | Accepted | Sprint 3 stopped at availability; Sprint 4 adds decision-only source selection and reservations. |
| Local secret storage first | Accepted | Sprint 3 stores secrets locally and redacts reads/logs; encryption is deferred to hardening. |
| Decision-only broker first | Accepted | Sprint 4 chooses sources and reserves account capacity without playback, proxy streaming, transcoding, or generated outputs. |
| Runtime URLs before outputs | Accepted | Sprint 5 clients use stable Media Router URLs that resolve through the Broker; STRM generation and output adapters remain deferred. |

## Open Decisions

- Migration tool choice.
- Whether the web UI needs authentication on the local network.
- Whether import jobs should be fully persisted.
- How long active stream history should be retained.
- How provider health should be scored.
- Whether plugins can be installed dynamically or only bundled.
- How catalog duplicate detection should score uncertain matches.

## ADR Template

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
