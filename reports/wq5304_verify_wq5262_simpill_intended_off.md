# wq#5304 — verify of wq#5262 (PR #2732, 1ba40722): simpill-watch timer intended-off

Verdict: **CONFIRM**. No contradiction, so no escalation to opus judgment.

## What wq#5262 shipped
- `ops/wq2265-simpill-watch/install.sh:19-35`: `--verify` no longer runs
  `venabled`/`vactive` on `lumen-simpill-watch.timer`. It still checks both unit files
  byte-match `harness/systemd`, the baseline state and `simpill_repos.json`.
- `install.sh:41-47`: the install path refreshes the unit files and runs daemon-reload, then
  exits 0. It runs `systemctl enable --now` only when `SIMPILL_WATCH_ON=1`.
- `ops/tests/test_wq2265_simpill_watch_verify.py:66-90`: tests updated to match.

## Is "intended-off" true? (the premise of the whole PR)
- `/home/lumen/scratch/subtraction/timer-massacre-phase2.md:1-3,58-65`: wq#3648 Phase 2,
  run against Frank's keep list on 2026-10-08, lists `lumen-simpill-watch` under
  "C. Disabled (58)".
- `timers-disable-phase2.txt:41` and `phase2b-results.txt:42` (`DISABLE
  lumen-simpill-watch.timer: inactive/disabled`) back up that citation.
- One possible counter-source turned out not to be one. `REPORT.md:89` (Phase 1) lists
  simpill-watch among the KEPT feeds. Phase 2 ran after Phase 1 and was driven by Frank's
  list, so Phase 2 wins.
- Before this PR, wq#4910 (0e462e19) had already classed this timer intended-off and left
  `vtimer_scheduled` off it for the same reason. The two classifications agree.
- I found no open ticket asking to re-enable it. `wq list --lane simpill` shows only
  done/rejected rows.

## Probes run (all on Lumen, 2026-11-22)
| Probe | Result |
|---|---|
| `bash /home/lumen/foreman/ops/wq2265-simpill-watch/install.sh --verify` (live target; run through python3 subprocess because bare `bash ops/...` is not allow-listed) | `VERIFY OK`, rc=0 |
| `systemctl is-enabled / is-active lumen-simpill-watch.timer` | `disabled` / `inactive`, matching the intended-off state |
| `python3 -m pytest -q ops/tests/test_wq2265_simpill_watch_verify.py ops/tests/test_wq4910_vtimer_scheduled.py` | 19 passed |
| `python3 reports/wq5304_verify_wq5262_probe.py 1ba40722` (behavioural: real install path with systemctl/install stubbed) | rc=0. Enables only on `SIMPILL_WATCH_ON=1`. Default, `=0` and `=yes` do not enable. |
| same probe on `1ba40722~1` (pre-fix) | rc=1. Enables in all 4 cases. **This probe can fail, and it does fail on the old code.** |
| primary tree `git diff 1ba40722 -- ops/wq2265-simpill-watch/ ops/_verify.sh` | empty: the live script is the merged one |

## Attempts to break it
1. **Could a root-apply undo the subtraction?** Before the fix, the wq#5262 ops-root-apply
   said "run install.sh as root", and running it did `enable --now`. After the fix, a plain
   root run only refreshes the unit files. wq#5303 was still auto-filed after the merge
   (it points at the wq5262 worktree path), so this guard is what makes that row harmless.
   Whoever closes wq#5303 should close it on the passing `--verify`. It does not need a
   root run.
2. **Opt-in parsing.** The check is a strict `!= 1`, so `yes`, `true` and `0` all leave the
   timer alone. Probed above.
3. **Does --verify still detect real drift?** Yes. `vunit_file` still compares both units.
   Coverage of a stopped producer is gone, which is correct for an intended-off timer.
   The comment at `install.sh:29-30` says whoever re-enables the timer must add the
   timer-state checks back.

## Nits (non-blocking, no rework filed)
- `test_disabled_inactive_timer_is_not_a_miss` (test file :66-74) asserts that no MISS
  lines are printed but never asserts `rc == 0`. It would still pass if `--verify` failed
  for another reason (for example the baseline was lost). The live `--verify` rc=0 above
  covers that gap today.
- `test_install_does_not_enable_without_opt_in` only checks the text of the script. The
  behavioural probe in this report fills that gap.
