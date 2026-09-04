# Plugin SDK

## Purpose

Media Router should support outputs and future service integrations through explicit plugin contracts. Plugins should extend the platform without reaching into unrelated internals.

The repository defines the plugin direction and a minimal abstract output type only. It
does not load third-party plugins. The implemented STRM and Live M3U outputs and Emby
adapter are built-in service modules; they must not be described as SDK plugins.

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

## Implementation sequence

STRM and Live M3U were implemented first as built-in outputs and validated the catalog,
stable URL, job, and disposable-artifact contracts. XMLTV/native output distribution is
the next concrete contract-discovery opportunity. A formal registry should follow real
service-boundary extraction rather than retroactively labeling current modules as
plugins. HDHomeRun remains post-1.0 ecosystem work.
