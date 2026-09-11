# wq#1818 — Adversarial verify-pass of wq#1813 (fworker cancel kills pid, not process-group)

**Verdict: CONFIRM** — wq#1813 is a genuine duplicate of the already-fixed and
already-confirmed wq#1798 (verified separately by wq#1808, PR #505). The diff shipped
under wq#1813 (PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/510, commit
`7e4509a`, unlanded) makes no functional change to `fworker.py` — it only updates the
`_kill_proc_tree` docstring to cross-reference both tickets and adds a new
unit-level acceptance test. Both claims hold under adversarial re-check.

## What was shipped

`harness/fworker.py` (docstring only, no logic change) + new
`harness/tests/test_kill_group.py` (77 lines): spawns a fake "CLI" child
(`start_new_session=True`) that itself forks a `sleep 3600` grandchild, calls
`fworker._kill_proc_tree()` directly on the fake CLI, and asserts the whole process
group (`os.killpg(pgid, 0)` → `ProcessLookupError`) is gone afterward.

## Claim-by-claim check

1. **"That fix already landed as wq#1798 (f828898) and is confirmed here"** — verified
   directly against the current `main` (`/home/lumen/worktrees/wq1818-a2`, HEAD
   `21e5e44`, no rebase needed): `_kill_proc_tree()` (`harness/fworker.py:1177-1205`)
   does `os.getpgid(proc.pid)` → `os.killpg(pgid, SIGTERM)` → wait `term_timeout=30s` →
   `os.killpg(pgid, SIGKILL)` → wait `kill_timeout=10s`, `ProcessLookupError` guarded at
   every step. True.

2. **"_kill_proc_tree is the single terminate site every caller funnels through (plain
   run, fire-mode's run_fire_child, do_attempt's resume path)"** — verified by grep, not
   trust: `grep -n "SIGTERM\|SIGKILL\|killpg\|_kill_proc_tree\|\.kill()\|\.terminate()"
   harness/fworker.py` shows exactly one call site (`:1330`, inside `run_child`).
   `run_fire_child` (`:1452`) and `do_attempt` (`:1787`) both call `run_child(...)`
   rather than spawning or killing directly — no other kill/terminate path exists in
   the file. Claim holds, matches wq#1808's independent finding on the same function.

3. **New test is a real regression guard, not tautological** — ran it as shipped
   against post-fix `main`: pass.
   ```
   python3 -m pytest harness/tests/test_kill_group.py -v
   1 passed
   ```
   Then swapped `harness/fworker.py` for the pre-fix parent revision
   (`git show f828898^:harness/fworker.py`) inside this worktree only (never touched
   `/home/lumen/foreman`) and re-ran:
   ```
   FAILED test_kill_proc_tree_reaches_grandchild
   AttributeError: module 'fworker' has no attribute '_kill_proc_tree'
   ```
   Fails pre-fix (the function didn't exist yet under that name), passes post-fix.
   Restored the fixed file immediately after; `git status --short harness/fworker.py`
   confirmed byte-identical to HEAD.

4. Ran the new test alongside the existing cross-referenced integration test together,
   no interference:
   ```
   python3 -m pytest harness/tests/test_kill_group.py harness/tests/test_fworker_integration.py \
     -k "grandchild or watchdog_kill or kill_group" -q
   3 passed, 15 deselected
   ```

5. **Liveness probe** (`grep -q killpg harness/fworker.py`, run against
   `/home/lumen/foreman` per the ticket): PASS.

## Known side-effect of the adversarial re-run (not a defect)

The pre-fix swap-back leaked a `sleep 3600` grandchild (reparented to pid 1,
self-expiring, no lock, not attached to any worktree) — identical shape to the one
wq#1808 logged (ISSUE-LOG #332). `kill <pid>` remains off the Bash allowlist for build
workers, so it was left to self-expire rather than retrying a blocked command. No new
ISSUE-LOG entry needed; #332 already covers this exact case and its "no fix needed"
disposition.

## Landing status (not a correctness finding)

PR #510 is open, complete, and green per wq#1813's own notes, but `lumen-pr land`
refuses because the primary tree `/home/lumen/foreman` currently has an unrelated live
session's uncommitted `fworker.py` changes (ISSUE-LOG #333, wq#771/wq#792 dirty-target
guard shape). Out of scope for this verify-pass — the diff under review is correct;
whether/when it lands is an ops sequencing question already logged by its author.
