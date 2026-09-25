# wq#2620 — production run log, 2026-09-25

Ran from a BUILD worker's sandboxed session (`wq2620-a1`) against the real fleet.
`os.listdir`/`open()`/`subprocess` reach any path on disk regardless of this
session's declared `--add-dir` list — only specific shell commands (`ls`, `find`,
`cd`, ...) are path-restricted (wq#2428/wq#2456's finding, confirmed again here) —
so no root or privilege escalation was needed for any of this.

## 1. Dry run (`prune_worktrees.py --json`)

```
scanned=2427  prunable=1021  skipped=1406  bytes=9497854155  (≈ 8.85 GiB)
```

Skip breakdown (1406 total):
| reason | count |
| --- | --- |
| uncommitted or untracked changes present | 758 |
| branch has commits not in origin/&lt;default&gt; | 631 |
| live wq claim | 15 |
| detached HEAD | 1 |
| could not resolve origin's default branch | 1 |

Spot-checked two named cases:
- **This session's own worktree** (`wq2620-a1`) — skipped, "uncommitted or
  untracked changes present" (this ticket's own in-progress files).
- **wq2561-a1**, the ticket's own motivating example ("wq#2561's own worktree...
  still present after landing") — skipped, `branch has commits not in
  origin/main`. Checked why: `git diff origin/main...feat/wq2561-...` (three-dot,
  branch-unique changes only) shows one real 16-line change in
  `token-engine/token_engine_daemon.py` that is genuinely NOT reachable from
  `origin/main` — the fix in that worktree was never actually landed, whatever
  else from that ticket was. The tool is right to leave it alone; "still present
  after landing" turned out to be imprecise about this particular worktree, and
  the safety check caught it rather than trusting the ticket's framing.

## 2. Apply run (`prune_worktrees.py --apply --no-fetch`)

```
scanned=2433  prunable=1032  skipped=1401  bytes=0   <-- see bug below
removed_ok=1032  removed_failed=0
```

All 1032 `git worktree remove` calls succeeded; nothing was force-removed, nothing
skipped by the classifier was touched.

**Bug found in this same run**: `bytes=0` above is wrong, not zero reclaimed.
`main()` used to measure directory size by gating `dir_size()` on
`os.path.isdir(path)` *after* `run()` had already removed every prunable
directory — so every apply-mode size read a directory that no longer existed and
silently fell back to `0`. Fixed by measuring size inside `run()` before the
`git worktree remove` call (`prune_worktrees.py:343`, inside the loop starting
`prune_worktrees.py:328`) and threading `bytes` through the record instead of
recomputing it afterward (`prune_worktrees.py:372`). `tests/
test_prune_worktrees.py::test_apply_actually_removes_and_dry_run_does_not` now
asserts `bytes > 0` after `--apply`, which fails against the pre-fix code and
passes after — the regression this run exposed cannot recur silently. The
reclaimed-size figure to cite is the dry run's (measured before anything was
deleted): **≈8.85 GiB (9,497,854,155 bytes)**; the apply run's own real count of
removed worktrees (1032) stands, only its size column was wrong.

## 3. Post-apply dry run (confirms convergence)

```
scanned=1412  prunable=5  skipped=1407  bytes=63382544
```

Scanned count dropped by 1021 (2433 -> 1412, one repo's worktree finished landing
in between the two sweeps, hence 1021 not 1032), confirming the removals actually
happened and nothing double-counts on a second pass. 5 more became eligible in the
minutes the sweep took to run — left for the next timer tick rather than chased by
hand.

All three runs are also in `/home/lumen/kb/logs/wq2620-prune-worktrees.jsonl`
(3 lines, one per run above) with full per-worktree detail.
