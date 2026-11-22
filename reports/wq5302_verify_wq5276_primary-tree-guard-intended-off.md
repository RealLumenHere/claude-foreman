# wq#5302 verify of wq#5276 (PR #2731, ac279cbd): CONFIRMED

Target: `ops/wq900-primary-tree-guard/install.sh --verify` stops asserting
`venabled`/`vactive` on `primary-tree-guard.timer`, which the 2026-10-08 subtraction
disabled on purpose.

## Checks run (2026-11-22)

| # | Check | Result |
|---|---|---|
| 1 | Intended-off claim: `/home/lumen/scratch/subtraction/timers-disable-list.txt:66` = `primary-tree-guard`; `phase2-results.txt:66` = `primary-tree-guard.timer: inactive/disabled`; `/home/lumen/agents/fleet-selfheal/selfheal.yaml:145` excludes `primary-tree-guard.timer` | Holds. Three independent sources agree |
| 2 | Live state: `systemctl is-enabled primary-tree-guard.timer` = disabled, `is-active` = inactive | Matches the intended-off state the fix encodes |
| 3 | Live unit-file checks (the only checks left in the verify block, install.sh:18-19): `diff` repo vs `/etc/systemd/system/` for `.service` and `.timer` | Both exit 0. The live `--verify` has nothing left to MISS |
| 4 | `python3 -m pytest -q ops/tests/test_wq5276_primary_tree_guard_intended_off.py ops/tests/test_wq4910_vtimer_scheduled.py` | 17 passed |
| 5 | Falsifiability: `test_old_asserts_would_have_failed_under_same_shim` puts the pre-fix asserts back under the same systemctl shim and requires the MISS lines. `test_verify_block_has_no_timer_state_asserts` pins the block text | The probe can fail |
| 6 | Drift on main since ac279cbd for the bundle or `ops/_verify.sh` | None (0 commits) |

I could not run `bash /home/lumen/foreman/ops/wq900-primary-tree-guard/install.sh --verify`
directly: the worker allowlist refused it. Check 3 covers each remaining assert by hand,
and check 4 runs the real script against the real `/etc` files with only systemctl shimmed.

## Non-blocking findings

1. `ops/tests/test_wq4910_vtimer_scheduled.py:173` drops the floor from `checked > 40` to
   `> 25`. This fix only needed a drop of 1, so up to 15 more timer bundles could fall
   out of the glob without the test noticing. Suggest `> 35`.
2. The comment at `install.sh:22` gives `scratch/subtraction/...` with no root, and that
   path does not exist inside the foreman repo. The README and the test docstring use the
   absolute `/home/lumen/scratch/...` path. The comment should use it too.
3. The test docstring says "a second run with the shim reporting enabled + active". The
   code actually re-inserts the old asserts under the same disabled shim. The docstring
   is wrong; the test logic is fine.
4. Residual risk, out of scope: the install path (`install.sh:31-33`) still runs
   `systemctl enable --now`. Running it as root will still undo the subtraction. The
   README now warns about this, and the verify no longer files a ticket that tells anyone
   to run it.

Verdict: CONFIRMED. The fix is correct and the live target passes. None of the findings
above changes that.
