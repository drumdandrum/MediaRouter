# Backup and Restore

MediaRouter backups protect the authoritative state under the Docker `/data`
mount without copying a live SQLite file directly. The backup command uses
SQLite's online backup API, validates the copied database, validates persistent
JSON, writes a hash inventory, and publishes one mode-`0600` archive only after
the staged backup is complete.

## State classification

The backup includes:

- `media_router.db`: catalog identities, sources, providers/accounts (including
  locally stored credentials), mappings, reservation/binding history, shadow
  observations, and output tracking metadata;
- `settings.json`, `wizard_state.json`, and output settings;
- `emby_integration_settings.json`, which may contain an API key;
- `.playback_ticket_secret`, when present, so outstanding ticket signatures and
  the installation identity remain stable;
- `jobs.json`, when present, as retained operational history.

The backup excludes generated STRM/M3U/XMLTV files, integration status cache,
in-memory/application logs, temporary files, test fixtures, and unknown files.
Generated outputs are disposable and should be rebuilt from restored catalog and
settings state. Provider input playlists and host-specific Compose/environment
files are outside `/data`; back them up separately when they are not otherwise
managed. Environment-provided secrets are also separate.

The archive contains credentials and key material even though its manifest does
not. Keep the archive on protected local storage, retain mode `0600`, and encrypt
or otherwise protect any off-host copy.

## Create and validate

Run from the repository checkout against the host directory mounted as `/data`:

```bash
scripts/media-router-backup create \
  --data-dir ./data \
  --destination /protected/backups/mediarouter-20260901T050000Z.tar.gz

scripts/media-router-backup validate \
  /protected/backups/mediarouter-20260901T050000Z.tar.gz
```

The launcher uses `PYTHON` when explicitly set, otherwise the repository
`.venv/bin/python` when present, and finally `python3`. The selected runtime must
have the dependencies from `requirements.txt` installed.

The destination must not already exist. Avoid configuration edits and output jobs
during backup so the small JSON files describe one coherent operator state. The
SQLite copy itself is transactionally consistent while MediaRouter remains
running.

The manifest records backup format, application version, database schema version,
UTC timestamp, included component names, byte sizes, SHA-256 hashes, and SQLite
integrity results. It contains no setting values or credentials.

## Inspect and dry-run restore

`inspect` performs the same full validation and prints the manifest. Restore dry
run validates compatibility and contents without creating the destination:

```bash
scripts/media-router-backup inspect /protected/backups/mediarouter-20260901T050000Z.tar.gz

scripts/media-router-backup restore \
  /protected/backups/mediarouter-20260901T050000Z.tar.gz \
  --destination /tmp/mediarouter-restore-check \
  --dry-run
```

Archives with unexpected members, duplicate entries, invalid JSON, altered hashes,
permissive filesystem mode, corrupt SQLite, broken foreign keys, unsupported backup
format, or a schema newer than the running code are refused.

## Restore and rollback

Never restore over a running or populated MediaRouter data directory. Stop only the
MediaRouter service, retain or rename the current data directory as the rollback
copy, and restore into a new empty directory:

```bash
scripts/media-router-backup restore \
  /protected/backups/mediarouter-20260901T050000Z.tar.gz \
  --destination /opt/mediarouter/data-restored
```

Point the MediaRouter `/data` bind mount at the restored directory and start the
same application version that created the backup, or a tested newer version. On
startup, the migration boundary upgrades older supported schemas and refuses newer
unknown schemas. Check `/api/health`, catalog counts, provider/account state,
integration mappings, and Broker status before rebuilding generated outputs.

Rollback is to stop MediaRouter, restore the prior bind mount/directory, and restart
the prior application image. Do not combine a restored older database with an older
image after a newer image has already migrated that database unless the release's
upgrade notes explicitly permit it. Database migrations are forward-only; backup
and retained-directory rollback are the recovery boundary.
