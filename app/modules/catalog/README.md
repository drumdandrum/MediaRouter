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

## Source-entry shadow ledger

The optional VOD source-entry ledger records sanitized observations beneath the
canonical catalog. It is disabled by default and enabled with:

```text
MEDIA_ROUTER_SOURCE_ENTRY_SHADOW_LEDGER_ENABLED=true
```

An observed import must also provide a persistent opaque `source_feed_id` UUID. On
first use, the ledger registers its exact provider, account, and VOD media scope in
`source_feeds`; later use must match all three values, including `NULL` values.
Conflicts skip shadow observation without changing the registered feed or the
authoritative import. The UUID belongs to the configured logical feed, not to a
catalog item, URL, path, or playlist position, so locator and token changes retain
identity when the configured UUID is retained. Imports without a safe UUID continue
normally but are not observed.

The ledger is diagnostic only:

- the existing importer remains authoritative;
- catalog IDs, source availability, outputs, playback, and Broker behavior do not
  consult it;
- provider identifiers are stored only as typed SHA-256 hashes;
- URLs, paths, credentials, tokens, and raw `#EXTINF` records are not stored;
- ledger finalization uses a separate post-import transaction, so ledger failure
  cannot roll back a successful catalog import;
- disabling the feature is the operational rollback.

Occurrence retention and pruning are not implemented. Until a retention policy is
added, operators should account for append-only occurrence growth. Live channels are
outside this observation phase. The registry does not generate feed UUIDs: callers
or configuration management remain responsible for creating and retaining one UUID
per logical feed.
