# Accounts Module

Owns IPTV account configuration, health state, priority, stream limits, provider-specific request metadata, and secrets handling.

Initial contracts to design before implementation:

- Account create/update/read models with redacted reads.
- Credential storage strategy.
- Provider connection test interface.
- Health scoring inputs consumed by the broker.
