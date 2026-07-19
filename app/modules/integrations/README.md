# Integrations Module

Owns adapters for external services.

The first implemented adapter polls Emby sessions from Media Router. An active session with durable catalog identity is authoritative: the adapter reuses a compatible provisional reservation or asks the Broker for an explicit-session reservation, then promotes, heartbeats, and grace-releases it through Broker lifecycle services. Runtime observations are optional correlation evidence, not an allocation prerequisite. The adapter never owns provider capacity, raw stream delivery, playback control, or complete Emby payload persistence.

Planned adapters:

- Emby plugin/webhook enhancements
- Jellyfin
- NextPVR
- Channels DVR
- IPTV Boss

Adapters should hide service-specific APIs behind small interfaces consumed by the wizard, dashboard, catalog, and output modules.
