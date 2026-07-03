# Principles

These principles are binding design rules for Media Router. Feature work should be checked against them before implementation.

## Principle 1: IPTV Boss Is The Editorial Source

IPTV Boss owns editorial decisions.

Media Router may read IPTV Boss exports, monitor export folders, and derive runtime catalog records from exported data. Media Router must not edit IPTV Boss data, rewrite IPTV Boss project files, or become a competing playlist editor.

Implications:

- IPTV Boss exports are inputs.
- Media Router imports are downstream copies or derived records.
- Any corrections to names, groups, logos, guide metadata, or playlist editorial choices should happen in IPTV Boss.
- Media Router should make it clear when data came from IPTV Boss.

## Principle 2: Media Router Owns The Runtime Catalog

Media Router owns the runtime catalog used for playback, broker decisions, generated outputs, and integrations.

The runtime catalog is derived from editorial sources such as IPTV Boss, existing STRM libraries, and future import sources. Once imported, Media Router may normalize, index, map, and route catalog entries for runtime use.

Implications:

- The runtime catalog is not the same thing as the IPTV Boss project.
- Broker URLs are based on Media Router internal IDs.
- Outputs are generated from Media Router catalog records, not directly from editor files.
- Runtime health, account mappings, and stream source availability belong to Media Router.

## Principle 3: Outputs Are Disposable

Generated outputs are build artifacts.

STRM files, generated M3U playlists, XMLTV files, HDHomeRun lineup responses, and similar outputs should be reproducible from the runtime catalog and settings. They should not be treated as authoritative state.

Implications:

- Outputs can be deleted and regenerated.
- Output plugins should avoid storing irreplaceable data in generated files.
- Rebuild operations should be safe and predictable.
- Existing user folder layouts may be preserved, but generated file contents remain disposable.

## Principle 4: Plugins Communicate Only Through The Service Layer

Plugins must use service-layer contracts.

Plugins must never directly access SQLite, database sessions, migration internals, or module-owned tables. They receive capabilities through approved service interfaces.

Implications:

- Plugins call catalog services, broker URL builders, settings services, path mappers, and event loggers.
- Database schema changes should not break plugin code directly.
- Plugins remain testable without a live database.
- Plugin permissions and dependencies are explicit.

## Principle 5: Every Catalog Item Has Exactly One Internal ID

Every movie, episode, and live channel has exactly one Media Router internal ID.

Internal IDs are stable runtime identifiers. A single media item must not be duplicated under multiple internal IDs. Provider URLs, playlist rows, and account-specific stream sources map to an existing internal ID.

Implications:

- The catalog must enforce uniqueness for internal IDs.
- Imports must match existing items before creating new ones.
- Multiple provider sources can map to one internal ID.
- Outputs must use internal IDs, not provider-specific URLs, as their stable reference.
- Duplicate detection is a catalog responsibility, not an output plugin responsibility.

## Review Checklist

Before implementing a feature, ask:

- Does this edit IPTV Boss data? If yes, reject or redesign it.
- Does this preserve Media Router as the runtime catalog owner?
- Can generated output be rebuilt from catalog and settings?
- Is a plugin trying to read SQLite directly? If yes, move that access behind a service.
- Could this create duplicate internal IDs for the same media item? If yes, add matching or uniqueness rules first.
