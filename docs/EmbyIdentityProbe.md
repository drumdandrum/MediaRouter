# Emby Identity Probe

The probe captures the identifiers Emby exposes at two boundaries:

1. The imported Live TV channel returned by `/LiveTv/Channels`.
2. Active playback returned by `/Sessions`, including the integration's existing full-item enrichment step.

It is read-only. It performs only Emby GET requests and does not allocate, confirm, heartbeat, release, or modify Broker reservations or bindings.

## Run

Start one MediaRouter-generated Live TV channel in Emby, then run from the MediaRouter source directory or container:

```bash
python scripts/emby_identity_probe.py \
  --channel-id 15747 \
  --output /data/emby-identity-playing.json
```

The script uses the Emby URL and API key already configured in MediaRouter. The API key is not written to the output.

For a complete stability check, capture the same channel:

1. Before playback.
2. During playback.
3. After restarting Emby.
4. After refreshing the tuner lineup.

Use separate output filenames for each capture.

## Acceptance criteria

A viable canonical identity must:

- contain the permanent MediaRouter catalog ID or an exact stable external key;
- appear in the imported channel DTO;
- remain available in the active-session or enriched-item DTO;
- survive Emby restart and lineup refresh;
- remain unique when channels have similar names.

Preferred evidence order:

1. MediaRouter runtime path containing `/r/live/<catalog-id>`.
2. An exact `mr:<catalog-id>` provider or tag marker retained by Emby.
3. Another verified stable Emby field mapped deterministically during import.

Channel-title matching is diagnostic only and must not become authoritative playback identity.

## Safety

Known credential and token keys are excluded or redacted. Provider URLs may still contain sensitive account query parameters, so review each JSON capture before committing or sharing it.
