# wq#5333: verify of wq#5325 (PR #2785, commit 6c0b3f29, merge 6d477a40): CONTRADICT (narrow)

**Verdict: CONTRADICT.** The shipped behaviour is correct and the bundle checks out live.
But the diff turned a test that was already committed, `ops/tests/test_wq5269_minerva_guard_intended_off.py`,
from green to red, and wq#5325 closed with "tests pass". The contradiction covers only that
suite regression. The install/verify logic itself holds up.

## What was checked

1. **Diff read cold.** `ops/wq3147-minerva-guard/install.sh:13` sets `INTENDED_ON=0` inside the file,
   so there is no env override. `:27-31` gates `venabled`/`vactive`/`vtimer_scheduled` behind it.
   `:41-47` gates `systemctl enable --now` behind it. With the flag at 0, install copies the files,
   runs daemon-reload and leaves the timer alone.
2. **Matches the sibling pattern.** It is the same shape as `ops/wq2819-iptables-drift-guard/install.sh:11,27,41-46`
   (wq#5266). The only differences are the flag name and the comment text.
3. **No other enabler.** `git grep minerva-guard.timer -- ops bin harness scripts` finds the timer only in
   this bundle, its README and its two tests. Nothing else runs `enable` on it.
4. **Live state.** `systemctl is-enabled` reports `disabled` and `is-active` reports `inactive` (exit 3),
   which is the intended state.
5. **Live --verify.** I ran `lumen-run start --name wq5333-verify -- bash /home/lumen/foreman/ops/wq3147-minerva-guard/install.sh --verify`.
   The unit `lumen-job-wq5333-verify-1795360819` finished DONE:0 and printed `VERIFY OK` / `LUMEN_JOB_EXIT=0`.
   A bare `bash .../install.sh --verify` is off the worker allowlist, which is why it went through lumen-run.
6. **New tests.** `python3 -m pytest -q ops/tests/test_wq5325_minerva_guard_intended_off.py`: all 6 pass.
   Among them is a falsifiability case: with the flag on and the timer disabled, a MISS is still raised.

## The break

`python3 -m pytest -q /home/lumen/foreman/ops/tests/test_wq5269_minerva_guard_intended_off.py` gives:

```
FAILED ...::IntendedOffTimerVerifyTest::test_verify_block_has_no_timer_state_asserts
AssertionError: <re.Match ... match='    venabled         lumen-minerva-guard.timer'> is not None : venabled
```

- `test_wq5269...py:70-74` is a static check. It requires that no `venabled|vactive|vtimer_scheduled
  lumen-minerva-guard.timer` line appear anywhere in the `--verify` block. wq#5325 deliberately
  put those lines back, wrapped in `if [ "$INTENDED_ON" = 1 ]`, so the regex matches them and the test fails.
- Before the change the test passed. On `6c0b3f29^:install.sh` the same regex matches 0 lines.
- The PR did not touch or retire that test. Its done note "6 new tests pass" only counted the new file.
- The two behavioural tests in test_wq5269, `test_disabled_inactive_timer_raises_no_miss` and
  `test_old_asserts_would_have_failed_under_same_shim`, still pass. The intent wq#5269 protected still
  holds; only the static assertion is out of date.

**Fix (not applied here: verify does not re-implement).** Change `test_wq5269...py:73-74` so it
accepts the asserts when they sit inside the `INTENDED_ON = 1` branch. One way is to strip the
`if [ "$INTENDED_ON" = 1 ]; then ... fi` span before running the regex. The other option is to
delete that one test, since `test_wq5325...test_shipped_intent_is_off` and `test_disabled_timer_raises_no_state_miss`
now cover it. Filed as a follow-up build ticket.

## Unrelated finding, not caused by wq#5325

Running the full `ops/tests` suite gives 446 passed and 4 failed. Three of the failures are in
`ops/tests/test_wq1609_scan_verify.py` (`test_reads_both_timer_states`, `test_timer_not_active_is_a_miss`,
`test_timer_not_enabled_is_a_miss`). They come from the wq1609 bundle (last touched by c866b57d,
"verify stops demanding intended-off timer live"), which has the same stale-static-test shape. PR
#2785 does not touch that bundle. I have noted it in the follow-up ticket so it isn't lost.
