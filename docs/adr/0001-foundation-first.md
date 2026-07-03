# ADR 0001: Foundation First

## Status

Accepted

## Context

The initial product vision is large: IPTV accounts, catalog management, stream brokering, failover, STRM generation, guide data, HDHomeRun compatibility, and multiple media server integrations.

Implementing all of that at once creates a brittle demo and hides important architectural decisions.

## Decision

Start with a foundation-only project:

- Minimal runnable FastAPI app.
- Explicit module folders.
- Shared domain contracts.
- Architecture and roadmap docs.
- No premature catalog, broker, account, or output implementation.

## Consequences

The project is less flashy at first, but future feature work has clearer boundaries and less rework.
