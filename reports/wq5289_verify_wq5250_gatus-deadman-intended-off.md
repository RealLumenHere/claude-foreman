# wq#5289 — verify wq#5250 (PR #2720): wq1184-gatus-health-checks --verify

Verdict: **CONFIRM.** Verifier: fresh instance, read cold. Date 2026-11-22.

## What the PR claims
wq#5250 (merged as `8e0c8b9d`, also `fc0eca2d`) says the ops-root-apply was a false
alarm. `lumen-cron-deadman.timer` is intended-off because of the 2026-10-08 subtraction.
`--verify` stops asserting it enabled/active. `install.sh` re-enables it only when
`LUMEN_CRON_DEADMAN_ENABLE=1`. `kb/gatus/config.yaml` was redeployed, and no root run
was needed.

## Checks run
| # | Check | Result |
|---|---|---|
| 1 | Live liveness probe `liveness.ops_verified('wq1184-gatus-health-checks')`, run from `/home/lumen/foreman/harness` | `('SATISFIED', 'wq1184-gatus-health-checks: VERIFY OK')` |
| 2 | Each `vfile`/`vunit_file` pair re-checked by hand with `git hash-object` on both sides | all 8 identical: config.yaml 81c61192, ntfy_relay 953114a2, cron_stamp a6153bb0, gatus.service 168654c8, relay.service 8acf9efa, deadman.service cd639c82, deadman.timer 220b090a |
| 3 | `systemctl is-enabled` / `is-active` | relay enabled+active; deadman timer disabled+inactive (the intended state); lumen-gatus disabled (intended, per header) |
| 4 | Is the intended-off claim real? | `scratch/subtraction/timers-disable-list.txt:78` = `lumen-cron-deadman`. `REPORT.md:67` ("Watchers-of-watchers / duplicate probes") names `cron-deadman`. wq#4910 had already dropped `vtimer_scheduled` for the same reason, so this PR finishes that change and does not invent a new exemption |
| 5 | PR test `ops/tests/test_wq5250_gatus_deadman_intended_off.py` | 5 passed |
| 6 | Can the test fail? Restored the pre-PR `install.sh` (`8e0c8b9d^`) into the worktree, re-ran it, then restored HEAD | 3 failed (verify asserts deadman on, no citation, unguarded `enable --now`). Worktree clean afterwards |
| 7 | Does anything else re-enable the timer? Ran `git grep "enable.*cron-deadman"` across foreman | only `install.sh:104`, which sits inside the opt-in guard |

## Attempts to break it
- **Is this a verify-weakening that hides a real failure?** No. The removed assertions
  contradicted a recorded, deliberate decision. The unit files are still byte-checked
  (`install.sh:44-47`), so a missing or drifted deadman unit still fails `--verify`. Only
  the enable/active state is no longer asserted.
- **Does a root re-run still undo the subtraction?** No. `install.sh:103-107` guards the
  `enable --now` call with `LUMEN_CRON_DEADMAN_ENABLE`, which defaults to 0.
- **Is the config redeploy real?** Yes. The deployed `kb/gatus/config.yaml` matches the
  bundle source byte for byte (check 2).

## Nits (non-blocking, not fixed here: verify-only)
- `install.sh:33-34`: the header comment still says "only the relay and the dead-man are
  checked for enablement". After this PR only the relay is checked. The comment is
  stale; behaviour is unaffected.
- Not changed by this PR, but a consequence of the subtraction: docs in other bundles still
  say `lumen-cron-deadman` reports stale stamps (`ops/wq1252-lumen-public-guardian/README.md:68`,
  `ops/wq1373-cf-token-liveness/README.md:55,104`). While the timer is off, nothing does.
- `$SECRETS_ENV` existence (`install.sh:57`) could not be checked by hand: guard.sh denies
  `/home/lumen/.secrets`. Check 1 passing covers it, because `vdone` would report the MISS.

## Tooling note
The worker allowlist refuses `bash /home/lumen/foreman/ops/<bundle>/install.sh --verify`,
plus `sha256sum`/`cmp` on these paths. This gap is already logged (ISSUE-LOG #1695,
#1737). I used the documented `liveness.ops_verified` workaround.
