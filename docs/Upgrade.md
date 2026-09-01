# Upgrade and Rollback

## Supported database path

MediaRouter supports fresh databases, unversioned databases created by tagged
v0.3 through current pre-ledger releases, and databases at any contiguous recorded
schema version not newer than the running build. Startup refuses unknown newer or
non-contiguous migration histories.

Before upgrading, create and validate a protected backup as described in
`BackupRestore.md`. Keep the prior application image and data directory available
until post-upgrade checks pass.

On startup, MediaRouter obtains an immediate SQLite write transaction for each
pending version. It applies that version, runs integrity and foreign-key checks,
writes the version record last, and commits. Failure rolls back that version and
prevents background polling from starting. Correct the cause and restart to retry.

After upgrade, verify:

```text
/api/health
Dashboard catalog and source counts
Providers and accounts
Broker status
Integration health and mappings
Output history
```

Generated STRM and M3U files do not need restoration or migration; rebuild them
after authoritative state is verified.

## Rollback

Database migrations are forward-only. Do not point an older application image at a
database already migrated by newer code unless that release explicitly documents
compatibility. Stop MediaRouter and restore the validated pre-upgrade backup into a
new empty data directory, then start the retained prior image against that restored
directory. Never overwrite the failed/upgraded directory in place; retain it for
diagnosis until recovery is confirmed.
