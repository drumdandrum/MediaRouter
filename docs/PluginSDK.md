# Plugin SDK

## Purpose

Media Router should support outputs and future service integrations through explicit plugin contracts. Plugins should extend the platform without reaching into unrelated internals.

The foundation phase defines the plugin direction only. It does not load third-party plugins yet.

## Plugin Categories

### Output Plugins

Generate or expose media server outputs from catalog and broker contracts.

Planned examples:

- STRM output
- M3U output
- XMLTV output
- HDHomeRun output
- REST API output

### Integration Plugins

Connect to external services.

Planned examples:

- Emby
- Jellyfin
- NextPVR
- Channels DVR
- IPTV Boss

## Output Plugin Contract

Conceptual interface:

```python
class OutputPlugin:
    name: str
    label: str
    description: str

    def status(self) -> dict:
        ...

    def build(self, context: OutputBuildContext) -> OutputBuildResult:
        ...
```

## Plugin Rules

- Plugins must not read provider credentials directly.
- Plugins must not access SQLite directly.
- Plugins must communicate through service-layer contracts only.
- Plugins must use broker URLs, not raw provider URLs, unless explicitly authorized by a contract.
- Plugins must report status in a user-readable way.
- Plugins must support dry-run or preview where destructive output writes are possible.
- Plugins must write useful diagnostics without logging secrets.
- Plugins must declare required settings and path mappings.
- Plugins must treat generated outputs as disposable artifacts.

## Suggested Plugin Metadata

```json
{
  "name": "strm",
  "label": "STRM Output",
  "version": "0.1.0",
  "category": "output",
  "requires": ["catalog", "broker", "settings"],
  "settings_schema": {}
}
```

## Build Context

Output plugins should receive a context object rather than constructing their own dependencies.

Context should include:

- Catalog reader.
- Broker URL builder.
- Settings reader.
- Path mapper.
- Event logger.
- Job cancellation signal.

Context must not include:

- Raw SQLite connections.
- SQLAlchemy sessions.
- Direct table repositories owned by another module.
- Provider credentials unless a narrowly scoped integration contract explicitly requires them.

## Plugin Lifecycle

Planned lifecycle:

1. Discover installed plugins.
2. Validate plugin metadata.
3. Register plugin contracts.
4. Load plugin settings schema.
5. Enable plugin instance through UI.
6. Run status/build operations through job system.

## First Plugins To Implement

1. STRM output
2. M3U output
3. HDHomeRun output
4. XMLTV output

STRM should come first because it validates the core catalog and broker URL model without requiring guide data.
