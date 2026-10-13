# wq#3790 — verify wq#3782: Prune 368 stale foreman worktrees

Verdict: **CONFIRM**. No contradiction found; no escalation to opus-5@high.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1784 (merged as `d973fd7f`,
branch commit `5511b39c`). Diff is docs-only: 45 lines appended to
`ops/wq2620-prune-stale-worktrees/RUN-LOG.md`. No code in `prune_worktrees.py`
changed — this ticket is an ops run + a writeup of it, not a feature change, so
the verify pass is about whether the writeup matches what actually happened on
the live fleet, not about a diff's logic.

## What I checked

1. **Diff fidelity**: `git diff d973fd7f 5511b39c -- ops/wq2620-prune-stale-worktrees/RUN-LOG.md`
   is empty — the merged commit on `main` is byte-identical to the PR branch tip.

2. **Timer restoration claim** ("restore the cut timer, not a new weekly cron",
   unit files "byte-identical", confirmed `enabled`/`active`):
   - `systemctl is-enabled lumen-prune-worktrees.timer` → `enabled`;
     `is-active` → `active` (live, independent of the PR's own prose).
   - `/etc/systemd/system/lumen-prune-worktrees.{timer,service}` read byte-for-byte
     identical to the committed copies in `ops/wq2620-prune-stale-worktrees/`.
   - `journalctl -u lumen-prune-worktrees.service` shows the real gap: last run
     **Oct 8 00:19:37** (`scanned=306 prunable=0`), nothing until
     **Oct 13 07:35:55** (`scanned=404 prunable=0`) — a 5-day gap, matching the
     writeup's "stopped... 2026-10-08... no sweeps for 5 days" exactly. The
     07:35:55 service run is systemd's `Persistent=true` catch-up firing the
     instant the timer was re-enabled, not a fabricated log line — independent
     corroboration the restore actually happened and actually re-armed the unit,
     not just "enabled" on paper.

3. **The actual prune (23 worktrees, ~424 MiB, 0 failures)**:
   - `/home/lumen/kb/logs/wq2620-prune-worktrees.jsonl` lines 28-31 (ts
     07:31:04-07:35:36Z) show the dry-run/apply pair: `scanned=426 prunable=23
     skipped=403 bytes=444480247`, action-type `prune` on all 23, matching the
     RUN-LOG quote exactly.
   - All 23 `prune`-action paths checked for existence on disk: all gone
     (`os.path.exists` false for every one). The deletion is real, not just
     logged.
   - Sampled 5 of the 23 removed branches across the fleet's current branch
     list (`wq3403-a1`, `wq3411-a1`, `wq3434-a2`, `wq3443-a2`, `wq3428-a2`) and
     ran `git merge-base --is-ancestor <branch> origin/main` on each: **all 5
     are ancestors of `origin/main`**, i.e. genuinely merged before removal. No
     evidence of data loss from the apply run.
   - Skip-breakdown "215 unmerged, 138 grace, 49 dirty, 1 detached" matches the
     dry-run log lines (28/29) exactly when categorized programmatically from
     the raw per-worktree reasons. (Line 30, taken 3 min later, had drifted to
     216/136/50/1 as individual worktrees crossed the grace-period boundary
     between runs — expected drift between two real invocations minutes apart,
     not a documentation error; the writeup cites a real run's numbers, not an
     invented average.)

4. **Scope-boundary / "could this probe fail" check** (wq#2630 doctrine): the
   run's own skip set includes grace-period, dirty-tree, unmerged-branch, and
   detached-HEAD cases together with the merged-and-removed case — i.e. the
   evidence checked isn't drawn solely from the "removed" population, it also
   confirms the tool correctly left non-candidates alone this run (403 of
   them), which is what would fail if the classifier were broken.

5. Existing test suite for the (untouched) tool still green:
   `pytest ops/wq2620-prune-stale-worktrees/` → 26 passed.

## Judgment call reviewed, not re-litigated

The writeup chose to leave the 403 skips alone rather than widen the rule to
match the ticket's looser "7 days, no active ticket" framing, citing the
wq#2561 precedent (ticket-done ≠ branch-merged). That's a documented,
reasoned call against the existing (already-verified, wq#2620) tool's
conservative criteria, not a code change — nothing to re-verify beyond
confirming the 403 skip reasons are real, which step 3 above did.

## Minor note (not a contradiction)

The ticket title/detail's "368" figure is the ticket's own framing at dispatch
(`wq show 3782`), not something the agent invented; the 5-day-gap arithmetic
checked out independently against the journal, so the "most had already
cleared via other activity" explanation for 368→23 is credible rather than a
dodge.

No findings rise to contradiction. Confirming on the board.
