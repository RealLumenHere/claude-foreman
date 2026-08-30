# wq#1100 — probe-state-drift / probe-log-repeats: drop retired units

Instance cleanup after the wq#1092 stale-target guard filed this ticket.
The guard already made the class harmless (STALE, never CRITICAL); this
removes the retired names so the probe stops spending a systemctl call on
them every run.

## Sweep

Every hardcoded `lumen-*` unit in `/home/lumen/sentinel/probes/sentinel_probes.py`
checked with `systemctl show <unit> -p LoadState` on 2026-08-30:

| unit | LoadState | action |
|---|---|---|
| lumen-prompt-engine | not-found | removed from probe_log_repeats + probe_state_drift |
| lumen-persona-engine | not-found | removed from probe_log_repeats |
| lumen-agent | masked (retired 2026-08-21) | removed from probe_log_repeats |
| lumen-planner / generator / evaluator | not-found | untouched, only referenced by probe_a5_health which wq#1095 unregistered |
| all others (dashboard, fleet-*, messenger, node-killer, self-healer, pokemon-live, litellm-auth-monitor) | loaded | kept |

prompt-engine and persona-engine went with the research mill on 2026-08-28
(unit files in `/etc/systemd/system/DISABLED-research-mill.bak/`).

## Apply

Live file edited in place (not in any git repo). Backup:
`sentinel_probes.py.bak.<ts>_pre_wq1100`. `sentinel_probes.diff` here is
the change.

## Verify

Both probes called directly after the edit:

    probe_state_drift -> (True, 'map/tailnet/units consistent', False)
    probe_log_repeats -> (True, 'no repeated-warning loops', False)

No STALE, no ticket filed. `grep '"lumen-prompt-engine"\|"lumen-persona-engine"\|"lumen-agent"'`
on the live file returns nothing.

## Rollback

`cp sentinel_probes.py.bak.<ts>_pre_wq1100 sentinel_probes.py`
