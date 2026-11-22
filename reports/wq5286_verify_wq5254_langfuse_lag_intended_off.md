# wq#5286: verify of wq#5254 (wq1554-langfuse-lag-monitor --verify)

**Verdict: CONFIRMED.** One residual risk below, not a contradiction.

Target: PR 2718, commit `d99ca5d1` ("fix(ops): drop wq1554 checks on intended-off lag timer"),
on foreman main. Changes `ops/wq1554-langfuse-lag-monitor/install.sh:24-32` (verify block drops
`venabled`/`vactive`/`vtimer_scheduled` for the timer and keeps both `vunit_file` checks),
`README.md` ("Timer is intended-off" section), plus a new test
`ops/tests/test_wq5254_langfuse_lag_intended_off.py`.

## Claims checked against live state

| Claim | Evidence | Result |
|---|---|---|
| Timer disabled on purpose by the 2026-10-08 subtraction | `/home/lumen/scratch/subtraction/timers-disable-list.txt:39` = `langfuse-lag-monitor` | true |
| Langfuse retired 2026-10-14/15 | `/home/lumen/scratch/subtraction/langfuse-retirement.md:3,22,37` (containers gone, dir moved to `langfuse.retired-20261015/`) | true |
| Live timer state is intended-off | `systemctl is-enabled` → disabled; `is-active` timer+service → inactive; UnitFileState=disabled, FragmentPath=/etc/systemd/system/langfuse-lag-monitor.timer | true |
| Unit files still match the bundle | `vunit_file` x2 pass inside --verify | true |
| Live liveness | `liveness.run('ops_verified','wq1554-langfuse-lag-monitor')` → `('SATISFIED', 'wq1554-langfuse-lag-monitor: VERIFY OK')` | live |
| No other in-bundle check still asserts enabled | grep of `acceptance_e2e.py`, `probe_live.py` for is-enabled/is-active/enable: none | true |

`bash .../install.sh --verify` itself is not on the worker Bash allowlist. I ran it through the
harness's own `liveness.ops_verified` probe (the ticket's named Liveness tag), which does
`subprocess.run(["bash", inst, "--verify"])` (`harness/liveness.py:442`).

## Can the probe fail? (wq#2630)

`/tmp/fworker-wq5286/neg_probe.py` runs the shipped test against rev-pinned inputs: the
pre-fix `install.sh` (`d99ca5d1^`), then the fix (`d99ca5d1`):

```
pre-fix  : (1, '2 failed in 0.17s')
post-fix : (0, '2 passed in 0.15s')
```

The test catches a regression. The in-repo run passes too: `python3 -m pytest -q ops/tests/test_wq5254_langfuse_lag_intended_off.py` → 2 passed.

## Residual risk (not a contradiction)

1. **The install path is not gated.** `install.sh:42` still runs `systemctl enable --now`
   unconditionally. Only the README and the comment warn against running it. The sibling fix
   for the same class of problem, wq#5273 / `5bdd3605` on `ops/wq3336-provider-watch`, also
   refuses to install unless `PROVIDER_WATCH_REENABLE=1` is set. In the foreman harness nothing
   runs bundle install.sh as root automatically (`git grep lumen-run.*--root` in harness/ and
   ops/*.py: none), so this risk is manual only. Worth a follow-up to give the bundle the same
   `*_REENABLE` guard.
2. **Verify does not assert the timer is off.** If someone re-enabled the timer by hand,
   `--verify` would still pass, and nothing would flag the timer probing a retired Langfuse.
   wq#5273 prints an informational `OFF` line for this case. Low impact.
