# Broker Module

Owns stream routing decisions.

Responsibilities:

- Choose a source account for a requested internal media ID.
- Respect account priority and maximum stream limits.
- Track active reservations.
- Support failover without client reconfiguration.
- Expose stable broker URLs such as `/movie/{id}`, `/series/{id}`, and `/live/{id}` once catalog contracts exist.
