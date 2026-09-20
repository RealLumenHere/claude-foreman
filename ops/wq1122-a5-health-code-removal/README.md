# wq#1122 — finish wq#1095: remove probe_a5_health code (option a)

wq#1095 only commented out the `probe-a5-health` PROBES tuple. Its ORCH
DIRECTION said to remove the code too and proved it with: grep the live file for
`lumen-planner|generator|evaluator` -> zero non-comment refs. The live file
still had `A5_SERVICES = ["lumen-planner", ...]` at :740 (1 hit).

Decision (option a, made under standing autonomy): finish it literally. The
a5 trio was retired 2026-08-21 and wq#1095 records it as never returning; the
stale-guard it once proved lives on in `_stale_or_none`, so the dead body has no
remaining role, and git-less resurrection is cheap from the `.bak` files.

## Applied to live /home/lumen/sentinel/probes/sentinel_probes.py
- `A5_SERVICES` / `A5_STATE` / `A5_MIN_VOL...` and `probe_a5_health()` (48 lines)
  replaced by a 3-line comment pointing at wq#1064 / #1092 / #1095.
- PROBES comment no longer claims the body is "left intact".
- Backup: `sentinel_probes.py.bak.20260920_pre_wq1122`. Diff: `sentinel_probes.diff`.

## State cleanup
`_stale_probe_tickets['probe-a5-health:lumen-planner']` was already absent from
`/home/lumen/sentinel/probes/state.json` at run time (keys: `_fires` + probe-*
entries only; no `_stale_probe_tickets`). Nothing to delete.

## Proof
- `ast.parse` of the live file: ok.
- Adversarial check from wq#1095: grep `lumen-planner|lumen-generator|lumen-evaluator|A5_|probe_a5`
  excluding `#` lines -> 0 hits (was 1).
- `test_probe_target_liveness.py` (wq#1092) replaced the scenario that called
  `probe_a5_health()` with one asserting it, `A5_SERVICES` and the PROBES entry
  are gone; passes against the new file, fails against the pre-change copy.
  The `[state-drift]` scenario fails both before and after (hathor/isis offline
  in tailscale; live fleet state, unrelated to a5).
- Run: `SENTINEL_PROBES_PATH=<live file> python3 ops/wq1092-probe-target-liveness-guard/test_probe_target_liveness.py`
