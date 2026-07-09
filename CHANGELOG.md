# Changelog

Media Router is a provider-agnostic media routing platform.

All notable changes to Media Router will be documented in this file.

---
# Unreleased

## Planned

- Runtime URL configuration
- Provider adapters
- STRM generation
- Live TV M3U generation
- XMLTV generation
- Local media providers
- HDHomeRun emulation
- Additional runtime improvements

---

# v0.6.0 – Runtime Source Resolution

**Release Date:** July 2026

## Added

- Runtime URL endpoints for:
  - `/r/live/{catalog_id}`
  - `/r/movie/{catalog_id}`
  - `/r/episode/{catalog_id}`
- HTTP redirect-based runtime resolution.
- Debug JSON resolve mode.
- Reservation-aware runtime routing.
- Runtime URL previews.
- Runtime preview API.
- Client labels.
- Adjustable reservation TTL.

## Improved

- Broker integration with runtime resolver.
- Reservation diagnostics.
- Runtime debugging.
- Credential masking in debug output.

## Notes

This release establishes the Media Router Runtime API.

Clients no longer need direct knowledge of provider URLs. All future integrations should consume Media Router runtime URLs instead.

---

# v0.5.1 – Broker Polish & Explainability

## Added

- Broker decision explanations.
- Candidate evaluation display.
- Reservation diagnostics.
- Broker status improvements.
- Structured broker responses.

## Fixed

- Fixed `[object Object]` rendering.
- Improved Broker UI feedback.
- Improved reservation refresh behavior.

---

# v0.5.0 – Broker Decision Engine

## Added

- Broker decision engine.
- Reservation system.
- Capacity-aware account selection.
- Priority groups.
- Weight-based routing.
- Reservation expiration.
- Broker API.
- Broker dashboard.

## Notes

This release introduced the core routing engine that determines which account should satisfy a media request.

---

# v0.4.0 – Providers & Accounts

## Added

- Provider management.
- Account management.
- Account priorities.
- Account weights.
- Maximum simultaneous stream limits.
- Provider persistence.
- Account persistence.

## Notes

Media Router now supports multiple providers with multiple accounts per provider.

---

# v0.3.0 – Catalog Engine

## Added

- Catalog database.
- Stable internal media IDs.
- M3U import.
- Live channel parsing.
- Movie parsing.
- Series parsing.
- Episode parsing.
- Source mappings.
- Catalog UI.
- Catalog APIs.

## Notes

The Catalog became the authoritative identity layer for all media.

---

# v0.2.1 – Foundation Polish

## Added

- Persistent settings.
- Improved setup wizard.
- About page improvements.
- Dashboard enhancements.
- Docker metadata.
- Version metadata.

## Fixed

- Settings persistence.
- Docker volume persistence.
- Foundation UI polish.

---

# v0.1.0 – Foundation

## Added

- Initial FastAPI application.
- Docker deployment.
- SQLite persistence.
- Dashboard.
- Settings.
- Jobs.
- Logs.
- Setup wizard.
- Documentation framework.

## Notes

Initial project foundation.