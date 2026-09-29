# wq#3120 — verify pass on wq#3119 (lumen-bin PR#198, d3a215d)

**Verdict: CONFIRM.** The fix does what it claims; probe reproduces on both sides.

## What was claimed (wq#3119 / ISSUE-LOG row 1040)
- `apply()` leaked its `lumen-pr start --worktree` worktree + local
  `chore/bin-drift-rescue-*` branch on every failed live `--apply`.
- The 3rd live run false-failed the post-ffprimary byte check on
  `commander-preload.cjs` even though the file had landed and live bytes matched.
  Root cause: `lumen-pr land` from a linked worktree already fast-forwards the
  primary as a side effect (wq#1536/wq#1490). The file was untracked pre-land, so
  after that implicit ff it was tracked+clean; the old "clear the overlap" step
  then unconditionally `os.remove()`d it (untracked-kind branch), `ffprimary`
  afterward was a no-op, and the post-check saw `exists=False`.
- Fix (d3a215d): the per-file "clear the overlap" loop now skips a path if
  `git status --porcelain` already reports it clean (i.e. already landed via the
  implicit ff); `branch` is hoisted above the `try`; a new `finally` unconditionally
  removes the worktree (`git worktree remove --force`) and deletes the local branch
  (`git branch -D`) whenever `wt` was set, regardless of success or exception.
  Test stub's `land` now also ff's the primary by default (mirroring the real
  behavior), with `STUB_LAND_NO_FF=1` preserving the old (refused-ff) shape, and
  `STUB_OPEN_FAIL` added alongside `STUB_FF_FAIL`/`STUB_LAND_FAIL`.

## Adversarial read
Traced every `return`/`raise` path out of `apply()`:
- Early returns (`nothing to rescue`, writer-active, dry-run) all happen before
  `wt` exists — no cleanup needed, none attempted. Correct.
- Any exception between a successful `lumen-pr start --worktree` and the final
  `return 0` (commit/open/land/ff failures, blob mismatches) goes through
  `except Exception` (restores from backup, returns 3) and then unconditionally
  through `finally` (wt is set → worktree+branch removed). This is exactly the
  wq#3119 leak path, now closed on every branch.
- The success path (`return 0` after `RESCUED`) also passes through `finally` —
  worktree/branch are reclaimed after a successful land too (nothing lost: content
  is already on `origin/main`). Confirmed by `assert_no_leftovers` in
  `test_proven_modified_rescued`/`test_proven_untracked_rescued`, which now run
  under both `land-ffs-primary` and `land-no-ff` parametrizations.
- The `finally` block calls the local `git()` helper without `check=True`, so a
  failed `worktree remove`/`branch -D` can't raise and mask the real exception
  from the `try`/`except` — it's deliberately best-effort, consistent with the
  comment ("holds nothing that is not in the backup or on origin").
- The new skip-if-clean check is per-file inside the loop, so a mixed batch (one
  file already ff'd, another still dirty) is handled correctly rather than via a
  single batch-level check.
- Gap noted, not a contradiction: there's no explicit test for `lumen-pr start`
  itself failing outright (`wt` stays `None`, so `finally` correctly no-ops since
  nothing was created) — logic is sound by inspection, just untested. Doesn't
  affect the wq#3119 claims.

## Probe (falsifiable, pinned to immutable commits — not a live mutable file)
Ran the actual repo test suite, not a synthetic probe:

1. Current fix (`d3a215d`, live in `/home/lumen/bin`):
   ```
   $ python3 -m pytest tests/test_bin_drift_rescue.py -q
   23 passed in 10.03s
   ```
2. Same (new) test file run against the pre-fix code, isolated via a scratch
   `git worktree add --detach /tmp/.../pre-fix bb9d12d` (the commit immediately
   before d3a215d) so the live `/home/lumen/bin` checkout was never touched:
   ```
   $ python3 -m pytest /tmp/.../pre-fix/tests/test_bin_drift_rescue.py -q
   13 failed, 10 passed in 10.03s
   ```
   The `test_proven_untracked_rescued` failure reproduces the exact live symptom:
   ```
   FAILED: access-jwt-guard.cjs is not byte-identical to the landed file after
   ffprimary (exists=False now_blob=none want=fc8feee531ba HEAD=858c24e
   ffprimary rc=0 out='')
   ```
   This matches ISSUE-LOG row 1040's cited numbers exactly (23/23 pass on the
   fix, 13 fail on the unfixed commit, `exists=False` reproduced) — the probe can
   and does fail on the population it's meant to catch, and passes on the fix.

Scratch worktree removed after the probe (`git worktree remove --force`); no
other worktree under `/home/lumen/bin` (16 active feature branches) was touched.

## Conclusion
No contradiction found. wq#3119's fix is correct, its test suite is a real
(falsifiable) regression guard for both the leak and the false-fail root cause,
and both numbers in the done-notes/ISSUE-LOG reproduce independently. Confirming
on the board; no escalation to opus-5@high.
