# wq#5312 — verify of wq#5267 (PR 2739): wq2836-ir-triage --verify

Verdict: **CONFIRMED** (the shipped claim holds). No contradiction, so no escalation.

Diff under test: commit e53d280e. `ops/wq2836-ir-triage/install.sh:10-14` adds the `IR_TRIAGE_ON=0`
intent flag. `install.sh:28-32` gates `venabled`/`vactive`/`vtimer_scheduled` on that flag, and
`install.sh:44-56` gates `enable --now` plus the smoke `start`. The commit also adds a README note
and `ops/tests/test_wq5267_ir_triage_intended_off.py` (6 tests).

## Claims checked

| Claim | Probe | Result |
|---|---|---|
| Timer is intended-off per the 2026-10-08 subtraction | `grep -n "ir-triage\|ir_triage" /home/lumen/scratch/subtraction/timers-disable-list.txt` | `2:lumen-ir-triage`. Holds. |
| Live state matches intent | `systemctl is-enabled` / `is-active lumen-ir-triage.timer` | `disabled` / `inactive`. Holds, and the root install was not run. |
| `--verify` is live (exit 0) | `liveness.ops_verified('wq2836-ir-triage')`, the harness's own probe (it runs `bash install.sh --verify`) | `('SATISFIED', 'wq2836-ir-triage: VERIFY OK')` |
| PR tests pass on main | `python3 -m pytest -q ops/tests/test_wq5267_ir_triage_intended_off.py` | `6 passed` |
| The tests can fail | Same test file run against `git show e53d280e^:ops/wq2836-ir-triage/install.sh` in a /tmp copy (`/tmp/fworker-wq5312/pre/ops`) | `6 failed`, including `test_disabled_timer_raises_no_state_miss` and the live-host test. These are real regression tests. |
| `--verify` still detects file drift | /tmp copy of the bundle with `liveness.OPS_ROOT` pointed at it; appended one byte to each source in turn | `.timer` → `STILL_BLOCKED: MISS differs: /etc/systemd/system/lumen-ir-triage.timer`; `ir_triage.py` → `STILL_BLOCKED: MISS differs: /home/lumen/minerva/ir_triage.py`. Holds. |
| Flipping intent re-arms the state asserts | Same /tmp copy, `IR_TRIAGE_ON=1` | `STILL_BLOCKED: MISS not enabled / not active / not scheduled ... VERIFY FAIL (3)`. Holds. |
| The env var cannot override the intent | `install.sh:14` assigns `IR_TRIAGE_ON=0` unconditionally, with no `${IR_TRIAGE_ON:-}` | Holds. This matches the comment on line 13. |
| A root install no longer undoes the subtraction | `install.sh:44-56`: `enable --now` and the smoke `start` exist only under the flag. The off branch only installs files and runs `daemon-reload`. | Holds. A later ticket (wq#5311) reached the same conclusion: no root apply was needed. |

## Attempts to break it (none succeeded)

- Ran the pre-fix script through the new tests to check whether they were vacuous. They are not: 6/6 fail.
- Checked whether hiding the timer asserts also hid the unit-file checks. It did not: `vunit_file`
  for the `.service` and `.timer` still runs unconditionally (`install.sh:19-21`), and drift is caught.
- Checked consistency with the sibling fixes. The pattern matches the `*_ON=0` flags in
  wq2434-drift-plan (wq#5263), wq2092-cert-watch (wq#5259) and wq2819-iptables-drift-guard.

## Observation (non-blocking, class-wide, not specific to this PR)

With `IR_TRIAGE_ON=0`, `--verify` checks nothing about the timer's state. Someone could re-enable the
timer by hand and the subtraction would be silently undone without `--verify` noticing. No sibling
`*_ON=0` bundle asserts the off state either, so this is a design choice of the whole class, not a
defect in this diff. A possible future hardening is a `vdisabled` helper in `ops/_verify.sh` for
intended-off bundles.
