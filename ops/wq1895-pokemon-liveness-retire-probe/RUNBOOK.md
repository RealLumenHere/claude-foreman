# wq#1895 — probe-pokemon-liveness: retire, target unit is masked

## Symptom

`probe-pokemon-liveness` (added wq#908, `/home/lumen/sentinel/probes/sentinel_probes.py`,
`sentinel-probes.timer` every 30 min) checks systemd unit `lumen-pokemon-live.service`.
The unit is retired:

    $ systemctl show lumen-pokemon-live.service -p LoadState -p ActiveState -p UnitFileState
    LoadState=masked
    ActiveState=inactive
    UnitFileState=masked

Auto-detected and filed by the wq#1092 stale-target guard (`_stale_or_none()`, same
`sentinel_probes.py`), which is exactly why this never turned into a false CRITICAL page
(the aq#899/wq#1064 shape) — it short-circuits to STALE (ok=True, critical=False) and
files one build-lane ticket, which is this ticket.

## Fix

Same instance cleanup wq#1095 and wq#1100 already did for other STALE-guarded probes:
once the guard has proven the target is genuinely retired (not a transient masking),
comment the probe out of the `PROBES` list so the 30-min cycle stops spending a
systemctl call on a check that can only ever return the same STALE string.
`probe_pokemon_liveness()` itself is left intact (not deleted) so it can be retargeted
if `lumen-pokemon-live.service` or a successor unit is ever brought back — see wq#908
for the frozen-turn-vs-crashed detection logic it implements.

Patch: `sentinel_probes.diff` in this directory, targeting the `PROBES` list entry.

## Apply

Live file is `/home/lumen/sentinel/probes/sentinel_probes.py`, outside any git repo and
outside this worker's granted read/write roots (`harness/fleet-exec-policy.yaml`
deliberately excludes `/home/lumen/sentinel/*` — sntl tokens, wq#1466). This ticket
prepares the patch; applying it to the live file (and taking the
`sentinel_probes.py.bak.<ts>_pre_wq1895` backup per the wq#1100 convention) requires a
lane with access to that path.

## Verify (post-apply, for whoever applies it)

    grep -n '"probe-pokemon-liveness"' /home/lumen/sentinel/probes/sentinel_probes.py

should show only the commented-out list line; `py_compile` on the file should stay
clean; next `sentinel-probes.timer` cycle should show 1 fewer live probe and no new
STALE-ticket for this key (already deduped via `state.json`'s
`_stale_probe_tickets["probe-pokemon-liveness:lumen-pokemon-live.service"]` regardless).

## Rollback

Uncomment the `("probe-pokemon-liveness", probe_pokemon_liveness, False),` line, or
restore from the `.bak.<ts>_pre_wq1895` snapshot.
