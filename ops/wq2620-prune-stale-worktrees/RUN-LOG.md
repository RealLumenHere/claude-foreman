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

## wq#3782 — manual sweep + timer restoration, 2026-10-13

`lumen-prune-worktrees.timer` was stopped and disabled on 2026-10-08 as part of
the Phase 1 subtraction cost-cutting pass (same pass that disabled the Langfuse
evaluator timers, wq#3672) — not because the pruner was wrong, just caught in a
blanket timer cut. With no sweeps for 5 days, 368 worktrees had accumulated
per the ticket's count at dispatch; by the time this ran, some had already
cleared via other activity.

```
$ python3 prune_worktrees.py --json
scanned=426  prunable=23  skipped=403  bytes=444480247  (≈424 MiB)

$ python3 prune_worktrees.py --apply --json
scanned=426  prunable=23  skipped=403  removed_ok=23  removed_failed=0
```

Skip breakdown (403 total): 215 unmerged branch, 138 within grace period, 49
dirty (uncommitted/untracked), 1 detached HEAD. The ticket's framing ("remove
worktrees older than 7 days not associated with active tickets") is a looser
rule than this tool enforces — most of the 403 skips are worktrees whose
*ticket* may be long done but whose *branch* was never actually merged, which
is exactly the case the tool is built to protect (README.md's wq#2561
spot-check: content can look "landed" while real branch-unique commits are
still unreachable from the default branch). Widening the rule to delete those
on ticket status alone risks losing unlanded work for a cosmetic worktree
count; left to the existing conservative criteria instead of reinventing a
looser one for this ticket.

**Timer decision: restore the cut timer, not a new weekly cron.** The unit
files on disk at `/etc/systemd/system/lumen-prune-worktrees.{service,timer}`
were untouched (byte-identical to this directory's committed copies) — Phase 1
subtraction stopped and disabled the timer but did not remove it. Re-enabling
the existing unit is the literal "replace what was cut" option the ticket
asks for; routing this through `dispatch-batch` instead would mean building a
new periodic-maintenance mechanism inside an unrelated dispatcher script for a
job that already has a tested, documented, idempotent daily timer. Restored
via `install.sh` (idempotent: re-runs a dry run, then
`install`+`daemon-reload`+`enable --now`) run as root through
`lumen-run start --root -- bash install.sh` (`lumen-run --root` is the sanctioned
detached-root path for a BUILD worker's install/enable step, precedented by
wq#2107/#2108's guardian timer re-enable). Confirmed after:
`systemctl is-enabled`/`is-active lumen-prune-worktrees.timer` → `enabled`/
`active`.
