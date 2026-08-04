# Mac mini integration-test harness

## Scope and environment boundary

The Mac mini Emby instance is the MediaRouter test/lab environment. It runs in
Docker and publishes Emby on host port `8597`. The family-use production host is
outside this harness and must never be configured, probed, or contacted.

The first harness phase provides isolated deployment mechanics and read-only smoke
checks only. Connecting Emby, importing fixtures, generating outputs, playback, and
enabling the source-entry shadow ledger require later, explicit approval.

| Component | Test value |
| --- | --- |
| Compose project | `mediarouter-mac-test` |
| MediaRouter container | `mediarouter-mac-test-app` |
| Host API | `http://127.0.0.1:18088` |
| URL visible from Dockerized Emby | `http://host.docker.internal:18088` |
| Test Emby host URL | `http://localhost:8597` |
| Test Emby URL from MediaRouter | `http://host.docker.internal:8597` |

No LAN/public binding, nginx, Cloudflare, tunnel, or proxy is part of this phase.

## Files and isolated state

Committed harness files:

```text
deploy/mac-mini/compose.test.yml
deploy/mac-mini/env.test.example
scripts/mac-mini-test
scripts/mac-mini-smoke
scripts/mac_mini_harness.py
tests/fixtures/mac-mini/
```

Generated test state is ignored by Git:

```text
.local/mac-mini/
  data/
  outputs/{movies,series,live}/
  logs/
  snapshots/
  evidence/
  feed-id
  secrets.env
```

The feed ID is a lab-only random UUID with mode `0600`. It is retained by `reset`
and removed only by confirmed `destroy`. It is never derived from a playlist
locator.

`secrets.env` is required to have mode `0600` when present. It is parsed as data,
never sourced by a shell, included in Compose, or archived. Stage 2 supports
exactly one credential assignment:

```text
MEDIA_ROUTER_TEST_EMBY_API_KEY=<dedicated-test-key>
```

Blank lines and lines whose first non-whitespace character is `#` are allowed.
No other variables, duplicate assignments, quoting, expansion, command
substitution, or shell metacharacters are supported. The value must be nonempty,
at most 1,024 characters, and consist only of letters, digits, `.`, `_`, `~`, or
`-`. Spaces around the assignment or value are rejected. LF and CRLF line
endings are accepted. Do not put an Emby URL, production credential, or
environment label in this file.

Provision the dedicated test key without putting it in shell history, command
arguments, documentation, or chat:

```sh
mkdir -p .local/mac-mini
umask 077
$EDITOR .local/mac-mini/secrets.env
chmod 600 .local/mac-mini/secrets.env
scripts/mac-mini-test credential-status
```

The final command prints only `valid` or a sanitized state such as
`missing_file`, `unsafe_mode`, `missing_key`, `duplicate_key`,
`unknown_variable`, or `invalid_format`. It exits zero only for `valid` and
nonzero for every other state. Never commit or print the key.

## Initialization

Initialize directories, copy the safe non-secret environment template, and create
the retained feed UUID:

```sh
scripts/mac-mini-test init
```

This does not build or start a container.

Render and safety-check the merged Compose configuration:

```sh
scripts/mac-mini-test config
```

The validator checks the actual merged JSON. It requires loopback port `18088`,
the exact test project/container names, test-local data and output mounts,
read-only fixtures, a disabled shadow ledger, and the Docker-internal runtime URL.
It rejects production paths/hosts, public bindings, reverse proxies, and unexpected
services or mounts.

Initialization and destructive commands also reject a symlinked `.local` parent or
`.local/mac-mini` root, so an operator-created link cannot redirect reset or destroy
outside the repository.

The override uses Compose `!override` for ports and volumes. Docker Compose
v2.24.4 or newer is therefore required.

## Lifecycle commands

These commands are implemented for the later Stage 1 deployment:

```sh
scripts/mac-mini-test start
scripts/mac-mini-test stop
scripts/mac-mini-test restart
scripts/mac-mini-test status
scripts/mac-mini-test health
scripts/mac-mini-test logs
scripts/mac-mini-test smoke
```

Every Compose invocation pins:

```text
project: mediarouter-mac-test
base: docker-compose.yml
override: deploy/mac-mini/compose.test.yml
environment: deploy/mac-mini/.env.test
```

`start` renders and validates the effective configuration before invoking Compose.
It starts only the MediaRouter service in the isolated project.

## Snapshots, restore, and rollback

Snapshots briefly stop only the test service:

```sh
scripts/mac-mini-test snapshot before-stage-1
```

A snapshot contains:

- isolated `/data` state except `data/emby_integration_settings.json`;
- isolated generated outputs;
- sanitized rendered Compose JSON;
- image ID when available;
- Git branch and commit;
- fixture digest;
- SQLite integrity result;
- only a boolean indicating whether `secrets.env` existed.

The raw Emby settings file is excluded before archive data is written, so its API
key is never staged, archived, hashed, or copied into evidence. The manifest may
record only allowlisted metadata: whether the file existed, its enabled state,
the validated local test URL, an optional validated MediaRouter URL, and
`api_key_included: false`. Unknown settings are omitted. Secret contents and the
retained feed UUID are not included in the state archive. The service restarts
only when it was running before the snapshot.

Restore requires the exact confirmation:

```sh
scripts/mac-mini-test restore before-stage-1 --confirm mac-mini-test
```

Restore validates archive member paths and rejects any snapshot containing a raw
`data/emby_integration_settings.json`. It creates a pre-restore snapshot using
the same exclusion, preserves `secrets.env`, `feed-id`, and the current machine's
Emby settings file byte-for-byte, restores only the remaining test data and
outputs, checks SQLite integrity, and restarts only if the service was previously
running. Snapshot metadata never creates an Emby settings file on a fresh
machine; credentials must be provisioned locally and separately. Snapshots do not
transport Emby credentials between machines or environments.

Restore never configures or contacts Emby. Absolute paths, traversal, links,
special filesystem members, and top-level content other than `data` and `outputs`
are rejected.

Stage 3 remains blocked until `stage2-emby-connected` can be created with these
protections, inspected successfully, and separately approved. Creating or
restoring a snapshot does not itself authorize imports or other Stage 3 work.

Tag the current test image for rollback:

```sh
scripts/mac-mini-test rollback-tag
```

Reset test data and generated outputs while retaining snapshots, secrets, and feed
identity:

```sh
scripts/mac-mini-test reset --confirm mac-mini-test
```

Reset removes the complete isolated `data` directory, including the local Emby
settings file, after confirmation. It does not display that file. Confirmed
`destroy` removes the entire exact `.local/mac-mini` root, including local Emby
settings, `secrets.env`, and `feed-id`; neither command transports credential
state elsewhere.

Completely remove only the pinned Compose project and `.local/mac-mini`:

```sh
scripts/mac-mini-test destroy --confirm mac-mini-test
```

The implementation rejects unexpected deletion roots and does not use broad
deletion globs.

## Synthetic VOD fixture

`tests/fixtures/mac-mini/catalog/vod-small.m3u` contains nine synthetic entries:

- five movies, including a unique item, normalized-title variation,
  remake-like pair, and identity-poor row;
- four episodes, including three complete structural identities from one
  synthesized series and one incomplete structural identity.

All identifiers are lab-only. Locators use the reserved `media.invalid` domain.
There are no remote artwork URLs, credentials, tokens, userinfo, or media files.
The expected manifest records the fixture digest, counts, and structural
characteristics without depending on implementation-derived catalog IDs.

## Read-only smoke checks

Once Stage 1 is separately approved and running:

```sh
scripts/mac-mini-test smoke
scripts/mac-mini-test smoke --repeat 20
```

The runner validates Compose isolation and fixture integrity, then reads:

- API health and system metadata;
- catalog summary;
- Emby status;
- Broker status and reservations;
- STRM and Live M3U settings;
- SQLite integrity and zero shadow-ledger observations;
- recent logs for `database is locked`;
- container identity/start time and descriptor count where available.

The runner is operator-read-only: it issues only GET requests and opens SQLite with
`mode=ro`. Some existing nominal GET service paths initialize schema or perform
Broker expiry maintenance internally; that pre-existing application behavior is
not changed or expanded by this harness.

Repeated mode performs bounded status reads and permits at most minor descriptor
noise. Lack of `/proc`-style descriptor data is reported as unavailable rather than
treated as failure on Docker Desktop.

The flags `--allow-import`, `--allow-output-generation`, and `--allow-playback` are
reserved and deliberately rejected. This phase has no mutating smoke mode.

## Emby target preparation

The reusable validator accepts a target only when:

- the environment label is exactly `mac-mini-test`;
- the credential-free HTTP authority is present in the local allowlist;
- the port is exactly `8597`;
- the hostname is `host.docker.internal`, `localhost`, or `127.0.0.1`;
- no path, query, fragment, userinfo, or production hostname is present.

The non-secret template allowlist contains:

```text
host.docker.internal:8597,localhost:8597
```

The validator does not contact the target or save MediaRouter settings. Stage 2
Emby configuration and connection testing require separate approval. A future
approved Stage 2 operation reads the validated
`MEDIA_ROUTER_TEST_EMBY_API_KEY` value in-process and supplies it as the
`api_key` field to `PUT /api/integrations/emby`; it is not an application
environment variable.

## Stage 1 readiness and evidence

Before Stage 1:

1. Run repository tests and `scripts/mac-mini-test config`.
2. Review the rendered mounts, port, image metadata, and disabled feature flag.
3. Create an empty-state snapshot and rollback image tag when an image exists.
4. Start only the isolated project.
5. Capture container/image IDs, health, effective configuration, SQLite integrity,
   logs, and smoke output under `.local/mac-mini/evidence`.
6. Stop on any production reference, public bind, unexpected mount, restart,
   database lock, secret-shaped output, or nonzero shadow observation.

Passing Stage 1 authorizes only isolated deployment and health checks. It does not
authorize Emby configuration, catalog imports, outputs, playback, or shadow-ledger
observation.

## Managed Mac mini test Emby deployment

The original `MacEmbyTester` container was created outside Compose. Its active
program-data directory is an anonymous Docker volume mounted at `/config`. Two
additional writable host binds were created with literal backslash-prefixed Linux
destinations (`\config` and `\media`); the former is empty and unused, while the
latter exposes `/Users/Shared/MediaRouter`. Port 8597 was also published on IPv4
and IPv6 wildcards. These mounts and bindings are not approved for integration
testing.

`deploy/mac-mini/compose.emby.test.yml` defines the replacement as a separate
`emby-mac-test` project. It pins the ARM64 Emby image by digest, publishes only
`127.0.0.1:8597:8096`, mounts one stable named volume at `/config`, and mounts only
the isolated movie and series STRM roots read-only. It does not mount Live output,
MediaRouter data, secrets, feed identity, snapshots, the repository root, or any
production path. A 60-second stop grace period allows Emby's service supervisor to
finish database shutdown before Docker escalates to a forced stop.

Initialize and render the local nonsecret configuration:

```sh
scripts/mac-mini-emby-test init
scripts/mac-mini-emby-test config
scripts/mac-mini-emby-test safety-check
```

The ignored `deploy/mac-mini/.env.emby.test` contains only the stable volume name
and absolute approved output paths. It must not contain API keys or other Emby
credentials.

### Protected config backup

The managed replacement workflow stops only the test Emby container before
backing up `/config`. The full archive is written beneath
`.local/mac-mini/emby-test/backups` with parent mode 0700 and archive mode 0600.
Unlike MediaRouter snapshots, this archive deliberately contains complete Emby
configuration and therefore contains credentials and authentication state. It
must never be committed, copied into a MediaRouter snapshot or evidence bundle,
printed, or shared between environments.

The helper image is pinned by digest and mounts the original volume read-only.
Archive validation rejects traversal, duplicate paths, links, and special members.
Offline SQLite integrity is checked where the local SQLite build can read the
copied databases. Only archive size, total file count/bytes, archive SHA-256,
source-volume name, timestamp, and helper-image digest are retained as sanitized
metadata.

The backup is restored into a new stable named volume. The original anonymous
volume is never modified. The old container is retained stopped under a timestamped
rollback name, so a failed replacement can return to the exact previous state
without restoring a database archive.

Backup mode is always explicit. The legacy mode preserves the original-container
identity and anonymous-volume guards and requires that original container to be
stopped:

```sh
scripts/mac-mini-emby-test backup --deployment legacy
```

The managed mode resolves the one running `emby` service from project
`emby-mac-test`, validates its exact image, loopback port, security settings, and
`emby-mac-test-config-v1` mount, then stops only that service for up to 60 seconds:

```sh
scripts/mac-mini-emby-test backup --deployment managed
```

It archives the stopped stable volume read-only, validates required Emby databases
and offline SQLite integrity, records sanitized Git/container/archive metadata,
starts only the managed service, and verifies server identity and MediaRouter
parity. Any failure after stop triggers a best-effort start of only that service.
Neither mode is inferred automatically, and neither mode operates on production.

Managed backup names use a UTC timestamp with one-second precision. Before opening
the archive path, the process atomically creates an exact per-timestamp claim
directory beneath the protected backup root. A simultaneous same-name attempt is
rejected before any per-run temporary file, archive, helper operation, service
stop, or volume mount; it does not alter the owner's claim, archive, temporary
metadata, or final metadata. Cleanup of every backup artifact requires
process-local ownership of that claim; a loser does not invoke artifact or
evidence cleanup helpers. After ownership is established, metadata
is validated and enriched in a mode-0600 temporary file, then published under
the final name with an exclusive same-filesystem hard link, so an existing final
name is never replaced and partially visible final metadata is impossible.

HUP, INT, and TERM retain statuses 129, 130, and 143. The first signal runs scoped
cleanup once; repeated termination signals are ignored only until that cleanup
finishes. Restart and cleanup failures are reported categorically but do not
replace the primary status. A validated archive and its published metadata remain
available if later service-recovery checks fail.

An uncatchable process termination can leave a hidden timestamp claim directory.
That stale claim conservatively blocks reuse of the same identity and cannot
damage another backup. It is not removed automatically: an operator must first
confirm no backup process owns it and that the matching archive/metadata state is
understood, then remove only that exact empty claim directory. No broad stale-claim
cleanup command is provided.

The controlled operation requires explicit confirmation:

```sh
scripts/mac-mini-emby-test replace --confirm emby-mac-test-replace
```

Library creation, library scans, playback, and runtime requests are deliberately
absent from this tool. Those remain blocked until the managed replacement passes
identity, mount, loopback-port, MediaRouter-polling, and stability validation.

### Rollback model

Rollback stops and removes only the replacement container, retains the cloned
volume and protected backup, renames the retained original container back to
`MacEmbyTester`, and starts it:

```sh
scripts/mac-mini-emby-test rollback --confirm emby-mac-test-rollback
```

Rollback restores the known previous deployment, including its wildcard port and
unsafe legacy mounts. It is an incident-recovery path, not the desired secure
state. The original container and anonymous volume must not be deleted until a
separate approval explicitly retires them.

### Managed replacement result (2026-08-02)

The controlled migration retained the original container, stopped, as
`MacEmbyTester-rollback-20260802-001317` and retained its authoritative anonymous
volume `be434b05f00eb341f06016c078d9b852990188a14ccead1884fdf2157061cc73`.
The managed replacement uses `emby-mac-test-config-v1`, preserved server identity
`312374cb311f4fa28ba32489efc20e39`, and passed loopback-port, exact-mount, and
MediaRouter-polling validation. The protected backup remains local and sensitive;
its contents must not be inspected or included in evidence.

Emby 4.9.5 can omit `StartupWizardCompleted` from the public system response.
Replacement verification rejects an explicit incomplete value and otherwise
confirms configured state from the preserved server identity and version,
required configuration databases, and MediaRouter's authenticated connection
test.

One legacy test Movies library already existed in the cloned configuration. The
migration neither created nor changed it, and no scan ran during migration. The
ability to suppress external metadata and image providers for new libraries is
still unresolved. Creating the two Stage 4A lab libraries and running their single
controlled scan therefore require separate explicit approval and validation.

### Legacy-library cleanup result (2026-08-02)

The protected managed backup `managed-config-20260802T211244Z.tar.gz` was used as
the rollback point for removing only stale library ID `49992`. Its archive SHA-256
is `0d10359ba2a22806badd5a66103d2316ebb67eb056936869be445d70f7fe454d`;
the archive remains local, ignored, mode 0600, credential-bearing, and must not be
opened or copied into evidence.

The global scan task's exact 12-hour trigger was captured, temporarily replaced
with an empty trigger array, and verified idle. Library `49992` was deleted once
through the Emby API with `RefreshLibrary=false`; its 4,999 stale movie items
reached zero without a manual scan or direct database mutation. The original
12-hour trigger was restored and verified, and no replacement library was created.
A later natural scheduled scan completed successfully with an empty library
inventory. Creating the two isolated Stage 4A libraries remains a separate,
explicitly approved operation. Restoring this backup or the retained original
container would also restore the stale legacy library and its previous externally
enabled metadata-provider state.
