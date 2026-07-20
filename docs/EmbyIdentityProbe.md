# Emby Identity Probe

The identity probe captures the exact identifiers Emby exposes at two boundaries:

1. The imported Live TV channel returned by `/LiveTv/Channels`.
2. The active playback returned by `/Sessions`, including the existing full-item enrichment step.

It is intentionally read-only. It performs only Emby GET requests and does not allocate, confirm, heartbeat, release, or otherwise modify Broker reservations or bindings.

## Run inside the MediaRouter container

Start playback of one MediaRouter-generated Live TV channel in Emby. Find the Emby channel `ItemId` from the integration page or an existing session response, then run:

```bash
python scripts/emby_identity_probe.py \
  --channel-id 15747 \
  --output /data/emby-identity-probe.json
```

The script uses the Emby server URL and API key already configured in MediaRouter. The API key is never written to the output.

If the container image does not include the repository `scripts` directory, run the command from a source checkout that mounts the same MediaRouter data directory and configuration.

## Capture sequence

For a useful comparison:

1. Generate or select one channel whose MediaRouter catalog ID is known.
2. Refresh the MediaRouter M3U tuner in Emby.
3. Run the probe before playback.
4. Start the channel in Emby and run the probe again.
5. Restart Emby, play the same channel, and run it a third time.
6. Refresh the tuner lineup and repeat once more.

Keep each output under a distinct filename, for example:

```text
emby-identity-before-playback.json
emby-identity-playing.json
emby-identity-after-restart.json
emby-identity-after-refresh.json
```

## Interpretation

A viable canonical identity must:

- contain the permanent MediaRouter catalog ID or an exact stable external key;
- appear in the imported channel DTO;
- remain available in the active-session or enriched-item DTO;
- survive Emby restart and lineup refresh;
- remain unique when multiple channels share similar names.

Preferred evidence order:

1. Exact MediaRouter runtime path containing `/r/live/<catalog-id>`.
2. Exact `mr:<catalog-id>` provider or tag marker retained by Emby.
3. Another documented stable Emby field that can be deterministically mapped at import time.

Channel titles are diagnostic only and must not become authoritative playback identity.

## Output safety

The output is projected to identity-relevant fields. Known token and credential keys are redacted or excluded. Review captures before committing them because provider URLs may still contain account-specific query parameters that are operationally sensitive.
