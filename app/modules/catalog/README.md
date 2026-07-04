# Catalog Module

Owns the internal media catalog and permanent IDs.

Responsibilities:

- Normalize M3U playlist metadata exported from editorial sources.
- Assign stable internal IDs to movies, episodes, and channels.
- Store mappings from internal IDs to one or more provider source URLs.
- Keep catalog identity separate from provider/source URLs.

Deferred:

- Existing STRM scanning.
- IPTV Boss folder watching.
- Broker routing and failover.
