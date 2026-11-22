# wq#5290: verify of wq#5257 (wq1704-missing-watchdogs, spof timers intended-off)

Verdict: **CONFIRMED**. Verifier: fresh instance, read cold. Target: claude-foreman PR #2723, merged as
`bef4bfba` (the version on origin/main for `ops/wq1704-missing-watchdogs/install.sh`).

## What the PR claims
`--verify` asserted all four `lumen-spof-*` timers enabled+active. `roundtrip` and `restarts` had been
disabled on purpose by the 2026-10-08 subtraction, so `--verify` could never pass, and the
ops-root-apply ticket's remedy (run install.sh as root) would have run `enable --now` on all four,
undoing the subtraction. Fix: `ENABLED=(daemons timers)`. Unit files for all four are still checked;
enabled/active/scheduled is only asserted for ENABLED; install enables/starts only ENABLED.

## Checks run
1. **Subtraction citation is real, not invented.**
   `grep -n spof /home/lumen/scratch/subtraction/timers-disable-list.txt timers-keep-phase2.txt` gives
   disable-list:71 `lumen-spof-restarts`, :72 `lumen-spof-roundtrip`; keep-phase2:28 `lumen-spof-timers`,
   :29 `lumen-spof-daemons`. `scratch/subtraction/REPORT.md:67` gives the reason: both were
   "watchers-of-watchers / duplicate probes". Neither the ISSUE-LOG nor the subtraction notes show any
   later decision to turn them back on (the only ISSUE-LOG hit is row 1711, written by this PR).
2. **Live state matches the intended shape.** `systemctl is-enabled/is-active`: daemons and timers are
   enabled and active; roundtrip and restarts are disabled and inactive.
3. **Live liveness gate.** `liveness.ops_verified('wq1704-missing-watchdogs')` returns `SATISFIED`
   ("... VERIFY OK"), so `--verify` exits 0 on the live host. This is the harness's own probe. The
   worker allowlist refuses a bare `bash .../install.sh --verify`.
4. **The PR's test passes.** `python3 -m pytest ops/tests/test_wq5257_spof_intended_off.py -q`: 6 passed.
   The bundle suite (`ops/wq1704-missing-watchdogs/tests`): 13 passed.
5. **Mutation: the tests can fail.** I copied the bundle and the test to `/tmp/fworker-wq5290/mut` and
   pointed all three `ENABLED` loops back at `CHECKS` (the pre-PR behaviour). Result: 3 failed, 3 passed.
   Failing: `test_off_timers_disabled_raise_no_state_miss`,
   `test_install_never_enables_or_starts_over_all_checks`, and `test_live_host_has_no_timer_state_miss`
   (this one runs against real systemd). The PR's own `test_on_timer_disabled_still_misses` covers the
   other direction: an intended-on timer going down still produces a MISS and rc != 0.
6. **Coverage is not weakened for the intended-on pair.** `vtimer_scheduled` (wq#4910) still applies to
   daemons and timers (`install.sh:31-37`). The unit-file drift check still covers all four
   (`install.sh:27-30`).

## Non-blocking findings
- `install.sh:48` would still overwrite `/home/lumen/bin/fleet-spof-watch` with the bundle copy. The live
  verify reports that file as `superseded` (it moved on in its own repo), so a root run of this bundle
  would downgrade it. This hazard predates the PR, and the PR correctly says no root install is needed.
  If anyone ever re-runs this bundle as root, that line should become supersede-aware first.
- `bef4bfba` has a `Co-authored-by:` trailer. House style is `Assisted-by:` only (wq#4796). Cosmetic.
- The rollback text (`install.sh:85`) still lists all four timers in `disable --now`. That is harmless,
  because disabling an already-disabled timer is a no-op.
