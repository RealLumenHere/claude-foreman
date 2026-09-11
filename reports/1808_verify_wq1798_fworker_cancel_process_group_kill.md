# wq#1808 — Adversarial verify-pass of wq#1798 (fworker cancel kills pid, not process-group)

**Verdict: CONFIRM** — the fix is correct, wired at the single terminate site every
run path funnels through, and the new regression test genuinely catches the bug it
claims to (fails against pre-fix code, passes against the fix).

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/505 (merged, squash), commit
`f828898`. `harness/fworker.py`:
- `_kill_proc_tree()` (:1177-1205): `os.getpgid(proc.pid)` then `os.killpg(pgid,
  SIGTERM)`, waits `term_timeout=30s`, escalates to `os.killpg(pgid, SIGKILL)` on
  timeout, waits `kill_timeout=10s`. `ProcessLookupError` guarded at each step (proc
  already gone).
- Wired into `run_child()`'s existing single terminate branch (:1330,
  `if killed_reason: ... _kill_proc_tree(proc)`), replacing whatever pid-only kill
  preceded it.
- New test `test_watchdog_kill_reaches_grandchild_process`
  (`harness/tests/test_fworker_integration.py:328`) + `fake_claude.py` scenario
  `hang_with_grandchild` (:344): fake_claude spawns a `sleep 3600` grandchild (inherits
  fake_claude's pgid, since fworker spawns fake_claude with `start_new_session=True`),
  records its pid, then hangs. Test asserts the grandchild is gone-or-zombie within 5s
  of the inactivity watchdog firing.

## Claim-by-claim check

1. **"start_new_session=True makes proc.pid double as its own pgid, reachable via
   killpg"** — confirmed at the spawn site, `harness/fworker.py:1229-1233`
   (`subprocess.Popen(..., start_new_session=True)`). POSIX guarantee, not
   fworker-specific: `setsid()` makes the new session leader's pid == its pgid.

2. **"This is the single terminate site every caller funnels through (plain run,
   fire-mode's run_fire_child, do_attempt's resume path)"** — verified by grep, not
   trust: `grep -n "run_child(\|\.terminate()\|\.kill()\|killpg\|Popen("
   harness/fworker.py` shows exactly one `Popen` spawn site (:1229), one
   `_kill_proc_tree` call site (:1330), and both `run_fire_child` (:1452) and
   `do_attempt` (:1787) call `run_child` rather than spawning their own subprocess or
   killing directly. No other `proc.terminate()`/`proc.kill()`/signal handler exists
   in the file. Every `killed_reason` branch (`inactivity`, `wall_clock`,
   `post_result_stuck`, `ticket_done`, `loop_break`, `turns_exceeded`, `reader_error`)
   falls through the same `if killed_reason:` guard into `_kill_proc_tree`. Claim
   holds.

## Probe run (adversarial — not just trusting the ticket's report)

Ran the new test as shipped: **pass**.
```
python3 -m pytest harness/tests/test_fworker_integration.py -k "grandchild or watchdog_kill" -q
2 passed, 15 deselected
```

Then, to check the test isn't a trivial/tautological pass, swapped
`harness/fworker.py` for the pre-fix parent revision (`git show f828898^:harness/fworker.py`)
inside this worktree only (not the shared `/home/lumen/foreman` checkout) and re-ran
the same test:
```
FAILED test_watchdog_kill_reaches_grandchild_process
AssertionError: grandchild pid 404650 still running after fworker cancel —
process-group kill did not reach it
```
Confirms the test is a real regression guard, not a no-op. Restored the fixed file
immediately after (`git status --short harness/fworker.py` clean, byte-identical to
HEAD). The leaked `sleep 3600` from that pre-fix run (pid 404650, reparented to pid 1)
is still alive as of this writing — `kill` is not on the Bash allowlist (see below),
so I could not reap it by hand. It holds no lock, is not attached to any worktree, and
self-expires on its own by design (`sleep 3600`, spawned ~16:5x UTC) — logged rather
than left silent.

Ran the full integration suite against the fixed file: 15 passed, 2 failed
(`test_denied_verb_blocked_at_hook_during_real_spawn`,
`test_launch_failure_blocks_with_infra_crash_class`, both asserting `wq_status ==
"blocked"` but getting `"claimed"`). Re-ran both in isolation against the **pre-fix**
file too — identical failures, identical assertion. Pre-existing, unrelated to this
diff (both scenarios crash immediately; neither exercises the watchdog-kill path at
all). Not a regression introduced by wq#1798's fix.

## Residual gap (not a contradiction, noted for the record)

`_kill_proc_tree` is called only from the `if killed_reason:` branch. `wq_heartbeat()`
(:1275) and the frame-parsing loop are not wrapped in try/except the way the
`wq_show` poll is (:1282-1288) — an uncaught exception there would propagate out of
`run_child` without reaching `_kill_proc_tree`, leaking the process group. This is
pre-existing behavior unchanged by this diff (same exception-safety shape existed
before f828898) and out of scope for wq#1798's fix, which is scoped to "signal the
group instead of the pid once we decide to kill" — not "guarantee we always decide to
kill." Not filing as a contradiction; flagging for a future ticket if it recurs in
practice.

## Bash allowlist gap hit during verification

`kill <pid>` is not on the Bash allowlist (`This command requires approval`). Logged
to `/home/lumen/kb/ISSUE-LOG.md`. Worked around via the harness's own guard (not
retried) — see log entry.
