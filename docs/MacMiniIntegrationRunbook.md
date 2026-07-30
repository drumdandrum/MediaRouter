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

`secrets.env` is also required to have mode `0600` when present. The first harness
phase does not read it into Compose or send it to any API. Never commit, print, or
place production credentials in that file.

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

- isolated `/data` state;
- isolated generated outputs;
- sanitized rendered Compose JSON;
- image ID when available;
- Git branch and commit;
- fixture digest;
- SQLite integrity result;
- only a boolean indicating whether `secrets.env` existed.

Secret contents and the retained feed UUID are not included in the state archive.
The service restarts only when it was running before the snapshot.

Restore requires the exact confirmation:

```sh
scripts/mac-mini-test restore before-stage-1 --confirm mac-mini-test
```

Restore validates archive member paths, creates a pre-restore snapshot, preserves
`secrets.env` and `feed-id`, restores only test data/outputs, checks SQLite
integrity, and restarts only if the service was previously running. It never
configures or contacts Emby.

Tag the current test image for rollback:

```sh
scripts/mac-mini-test rollback-tag
```

Reset test data and generated outputs while retaining snapshots, secrets, and feed
identity:

```sh
scripts/mac-mini-test reset --confirm mac-mini-test
```

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

Repeated mode performs bounded status reads and permits at most minor descriptor
noise. Lack of `/proc`-style descriptor data is reported as unavailable rather than
treated as failure on Docker Desktop.

The flags `--allow-import`, `--allow-output-generation`, and `--allow-playback` are
reserved and deliberately rejected. This phase has no mutating smoke mode.

## Emby target preparation

The reusable validator accepts a target only when:

- the environment label is exactly `mac-mini-test`;
- the credential-free HTTP authority is present in the local allowlist;
- the hostname is `host.docker.internal`, `localhost`, or `127.0.0.1`;
- no path, query, fragment, userinfo, or production hostname is present.

The non-secret template allowlist contains:

```text
host.docker.internal:8597,localhost:8597
```

The validator does not contact the target or save MediaRouter settings. Stage 2
Emby configuration and connection testing require separate approval.

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
