# Accounts Module

Owns provider-agnostic account/connection configuration, health state, priority, stream limits, request metadata, and secrets handling.

Sprint 3 responsibilities:

- Provider records for IPTV, HDHomeRun, NextPVR, Local Files, Emby, Jellyfin, and Other.
- Account/connection create/update/read models with redacted reads.
- Lightweight non-streaming connection tests.
- Health and priority metadata consumed by the future broker.

Deferred:

- Broker routing and failover.
- Stream slot consumption.
- Encrypted local secret storage.
