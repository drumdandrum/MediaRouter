# Integrations Module

Owns adapters for external services.

The first implemented adapter polls Emby sessions from Media Router. An active session with durable catalog identity is authoritative: the adapter reuses a compatible provisional reservation or asks the Broker for an explicit-session reservation, then promotes, heartbeats, and grace-releases it through Broker lifecycle services. Runtime observations are optional correlation evidence, not an allocation prerequisite. The adapter never owns provider capacity, raw stream delivery, playback control, or complete Emby payload persistence.

`POST /api/integrations/emby/mapping-audit/preview` provides a separate read-only diagnostic scan for channels, movies, series, and episodes. It reports manual, exact, normalized-title, placement-title, ambiguous, unmatched, and unsupported results using only media-appropriate evidence. Persisted Emby ItemId and MediaSourceId mappings and placement titles are channel-only; episodes require series, season, and episode structure. Returned detail is sanitized and bounded, complete-scan status is explicit, and grouped counts cover the scanned population rather than only the returned detail page.

The audit never writes mappings, calls channel refresh, changes playback identity, or applies VOD matches. It performs no fuzzy or runtime title matching. Movies, series, and episodes are not apply-eligible, and the UI is intentionally deferred until the API contract is validated.

Planned adapters:

- Emby plugin/webhook enhancements
- Jellyfin
- NextPVR
- Channels DVR
- IPTV Boss

Adapters should hide service-specific APIs behind small interfaces consumed by the wizard, dashboard, catalog, and output modules.
