# wq#5328 — verify of wq#5314 (PR #2774, commit 0ca0bc3d): CONFIRMED

Verifier: fresh instance, adversarial verify-pass. Target: `ops/wq2086-presentation-stability/install.sh`
gating of `systemctl enable --now lumen-presentation-stability.timer` on the in-file intent flag.

## What was checked

1. **Diff read cold.** `install.sh:23` adds `PRESENTATION_STABILITY_ON=0` (plain assignment, so an env
   var cannot override it). Root branch (`install.sh:86-92`) runs `enable --now` only when the flag is 1,
   otherwise installs units + `daemon-reload` and prints "intended-off". Unprivileged branch
   (`install.sh:99-104`) no longer lists the enable as an owed step while 0. `--verify`
   (`install.sh:37-41`) asserts venabled/vactive/vtimer_scheduled only when 1. Same shape as wq#5266
   (8cec2566, `IPTABLES_DRIFT_GUARD_ON`).
2. **No other un-gated enable path.** `grep -rn "enable --now lumen-presentation\|enable lumen-presentation"`
   over the repo (minus reports/) hits only `install.sh:87` and `install.sh:100`, both gated.
   `pr_body_wq2086.md` mentions only the rollback `disable --now`.
3. **Regression test passes on the fix:** `ops/tests/test_wq5314_presentation_stability_intended_off.py`
   10 passed, including `test_live_host_has_no_timer_state_miss` (ran, not skipped: the unit file
   exists on Lumen). The bundle's own tests also pass (32 total with `ops/wq2086-presentation-stability/`).
4. **Probe can fail (mutation on pre-fix tree).** Detached worktree at `0ca0bc3d~1` with the new test
   copied in: 8 failed, 2 passed, matching the author's claim. The two core regressions fail for the
   right reason, not on a setup assert:
   - `test_root_replay_does_not_enable_timer`: shim log `['daemon-reload', 'enable --now lumen-presentation-stability.timer']` != no enables.
   - `test_unprivileged_branch_does_not_owe_enable`: `'systemctl enable --now'` found in output.
5. **deploy_triage prose (`ops/deploy_triage.py:94-98`).** Now says "installed but disabled on purpose".
   KEEP_LIVE + `units=[timer]` stays correct: the register's evidence is presence in
   `systemctl list-unit-files` (`deploy_triage.py:55-58`, `:289-292`), and a disabled timer is still
   present. `ops/tests/test_wq2985_deploy_triage.py` gives 15 passed.
6. **Live state unchanged.** `systemctl is-enabled` reports `disabled`, `is-active` reports `inactive` (2026-11-22 14:3x UTC).

## Minor gaps (not contradictions)

- `test_every_enable_is_gated` and `test_unprivileged_branch_does_not_owe_enable` match only the literal
  `systemctl enable --now`. A future printed owed step `systemctl enable <timer>` (without `--now`)
  would get past both. A real root-branch `enable` call would still be caught by the shim-log
  assertion in `test_root_replay_does_not_enable_timer`. No follow-up filed: the current file is correct.
- The commit carries a `Co-authored-by: Lumen Industries` trailer. Sibling 8cec2566 has the same
  trailer, so it comes from lumen-pr's own footer and not from the author.

## Verdict

CONFIRMED. The fix does what wq#5314 asked: root replay no longer undoes the 2026-10-08 subtraction,
the unprivileged branch no longer owes the enable, and the regression test fails on the pre-fix code.
