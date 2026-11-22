# wq#5308 — verify of wq#5263 (PR 2734): wq2434-drift-plan --verify

Verdict: **CONFIRMED**. No contradiction, no escalation.

## What the PR does

`ops/wq2434-drift-plan/install.sh` gains `DRIFT_PLAN_ON=0` (install.sh:14). With it at 0:
- `--verify` no longer asserts the timer is enabled, active or scheduled (install.sh:28-32);
  the .py, .service, .timer byte checks and the dashboard `vcontains` stay.
- install no longer runs `systemctl enable --now lumen-drift-plan.timer` (install.sh:53-58)
  and skips the timer health check (install.sh:62-64).

The ticket asked for the bundle to be run as root. The author chose not to, because doing
that would enable a timer the 2026-10-08 subtraction turned off on purpose.

## Probe: `reports/wq5308_verify_wq5263_probe.py` (exit 0 = confirmed)

| Check | Result |
|---|---|
| Live `--verify`, through the harness's own `liveness.ops_verified` | `SATISFIED, VERIFY OK` |
| Drift still caught: scratch copy in `/tmp/fworker-wq5308/ops`, one byte appended to drift_plan.py | `STILL_BLOCKED: MISS differs: /home/lumen/minerva/drift_plan.py` |
| Same test on lumen-drift-plan.timer | `STILL_BLOCKED: MISS differs: /etc/systemd/system/lumen-drift-plan.timer` |
| The gate can still fail: scratch copy with `DRIFT_PLAN_ON=1` | `STILL_BLOCKED: not enabled (disabled), not active (inactive), not scheduled (SubState=dead)` |
| install path: the only executable `enable --now` sits in the `DRIFT_PLAN_ON = 1` branch | count=1, gated |
| Is intended-off grounded? `/home/lumen/scratch/subtraction/timers-disable-list.txt:56` (mtime Oct 8) | `lumen-drift-plan` |

Live state: `systemctl is-enabled lumen-drift-plan.timer` returns `disabled`; `is-active` returns `inactive`.
PR test: `python3 -m pytest -q ops/tests/test_wq5263_drift_plan_intended_off.py` gave 6 passed.

## Attempts to break it

- **Did someone decide to turn the timer back on after the subtraction?** Since 2026-10-08,
  `git log` on the bundle shows only 0e462e19 (wq#4910, which already treated this timer as
  intended-off) and this fix. No ticket asks for it back.
- **Is the check deleted, or only switched off?** Switched off. Setting `DRIFT_PLAN_ON=1`
  brings back all three timer checks, and they fail against the live box.
- **Could the fix hide a real failure?** No. Byte drift in the shipped files still fails
  `--verify`.
- **A mistake in my own probe.** Its first run counted two `enable --now` matches. The
  second match is the wq#5263 comment at install.sh:25, not code. The probe now counts
  executable lines only.

## Residual (not a PR defect)

With the timer off, `/home/lumen/minerva/drift_plans/index.json` refreshes only when
install.sh runs, so `/api/v1/drift-plans` serves stale plans. That is the subtraction's
intended cost. Turning the timer back on means setting `DRIFT_PLAN_ON=1`, which is the
reversal path the PR documents.

The verifier's sandbox refuses a direct `bash .../install.sh --verify`. The live check
went through `liveness.ops_verified` instead, which runs the same command (liveness.py:442).
