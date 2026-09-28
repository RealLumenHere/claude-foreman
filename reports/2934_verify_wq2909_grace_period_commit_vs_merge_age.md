# wq#2934 — verify-pass: wq#2909's grace-period fix (PR #1239)

Verifier: claude-sonnet-5 @ high, fresh instance. Worktree `wq2934-a1`.
Subject: `ops/wq2620-prune-stale-worktrees/prune_worktrees.py`, as landed by
wq#2909 on main as **a361e80** (`fix(prune): add grace period before pruning
worktrees`; content-identical to **46d346f2d6**, the SHA named in wq#2909's
own done-notes and the Gitea PR — the two are separate commits on the same
history, verified with `git diff 46d346f2d6 a361e80 -- ops/wq2620-* ops/wq2909-*`
producing no output; `a361e80` is the one actually reachable from
`origin/main`).

## Verdict: CONTRADICTED

The fix does not do what it says it does. Its docstring and README both claim
"a same-day merge gets one week to be reverted before its worktree is gone for
good" (`prune_worktrees.py:24-26`, README.md:15,29). In fact the grace period
is measured from the branch tip **commit's own timestamp**
(`merge_age_days()`, `prune_worktrees.py:190-206`, using `git log -1
--format=%ct <branch>`), not from when the branch actually became an ancestor
of `origin/main`. Any ticket whose last commit predates its actual landing by
more than the grace window — work that sat blocked, on hold, or in review
before merging — gets **zero** revert protection: the janitor can prune it
the moment the merge lands, exactly the failure mode wq#2909 was filed to fix
(`prune_worktrees.py:190-197`'s own docstring: "the janitor pruned a merged
+clean worktree the moment `is_merged` went true, with no window for a
same-day revert to still find it").

## 1. What the fix actually does

`classify()` (`prune_worktrees.py:287-323`) requires, in order: registered,
not locked, not detached, clean (`is_clean`), merged into `origin/<default>`
(`is_merged`, an ancestor check), then `age = merge_age_days(repo, branch)`
must be `>= grace_days` (default 7). `merge_age_days` reads the branch tip's
committer timestamp via `git log -1 --format=%ct <branch>` and diffs it
against wall-clock now. This is a *commit-age* clock, not a *merge-age*
clock. The two coincide only when a branch's last commit and its landing
happen close together in time — true for the fast claim→commit→land loop
this fleet mostly runs (wq#2909 itself: claimed 08:19:24, committed
08:26:46, done 08:27:38, all inside one session) but not guaranteed, and nothing
in the code enforces or even checks that assumption.

## 2. Reproduction

Probe: `reports/wq2934_probe_grace_period_commit_vs_merge_age.py` (committed,
self-contained, uses `prune_worktrees.merge_age_days`/`run` directly against a
throwaway git repo — no fixtures depended on outside itself).

Scenario: a branch's only unique commit is dated (via `GIT_COMMITTER_DATE`)
10 days in the past — modeling a ticket whose work finished, then sat
blocked/on-hold, then landed today. The branch is merged into `origin/main`
**right now** (today's wall-clock time) with `git merge --no-ff` + push, so
by the fix's own stated goal this merge event should open a fresh 7-day
revert window starting now.

Run output:
```
branch merged into origin/main just now: True
merge_age_days() reports: 10.00 days (tip commit was authored 10d ago)
classify() action='prune' reason='merged into origin/main, clean, no such ticket in wq db'
CONTRADICTED: worktree merged moments ago is immediately prunable -- the grace period gave ZERO protection.
```

`classify()` returns `prune` immediately — the janitor would delete this
worktree on the very first run after the merge, with no revert window at all.
This is the identical symptom class wq#2909 was opened against (a
merged+clean worktree pruned with zero warning), just reached through a
different route: instead of `is_merged` going true the instant of merge (the
original bug, now fixed for the fast-loop case), an *old commit date on a
late merge* zeroes out the grace period the same way.

## 3. Why this is realistic, not contrived

The wq schema itself anticipates tickets that don't land in one shot:
`hold_by`/`hold_expires`, `blocked_at`/`block_reason`, `reopened_count`,
`reopened_at` are all live columns (see `wq show 2934`/`wq show 2909`
output). A quick query against the live queue db
(`/home/lumen/agents/work-queue/queue.db`, read-only) over the 2000 most
recent `done` tickets with both `claimed_at` and `done_at` set found **50**
with either a >2-day claim-to-done gap or a nonzero `reopened_count`,
including wq#1670 at a 15.7-day claim-to-done span — longer than the 7-day
grace default on its own. A ticket that commits its work early, then sits
blocked/reopened for more than a week before finally landing, is exactly the
`merge_age_days` failure mode above, and this fleet's own history contains
tickets in that shape.

## 4. Test-suite gap

All 20 existing tests pass (`python3 -m pytest
ops/wq2620-prune-stale-worktrees/tests/test_prune_worktrees.py -q` → `20
passed`), so nothing here is a regression against the suite as shipped — but
none of the two grace-period-specific tests
(`test_merged_worktree_within_grace_period_is_skipped`,
`test_merged_worktree_past_grace_period_is_prunable`,
`tests/test_prune_worktrees.py:278-298`) construct a branch whose commit date
and merge time diverge; the "past grace period" test reaches its 8-day age
by monkeypatching `merge_age_days` directly rather than by merging an
actually-old branch, so the suite never exercises real git ancestry with a
commit older than the branch's merge event. That's the gap this probe fills.

## 5. Scope note

wq#2909's other three FIX items (restic `--cleanup-cache`, the root→scratch
archive/restic move, the fleet-audit 75% alarm line) were explicitly filed as
follow-ups or recorded-not-gated decisions per wq#2909's own done-notes, not
part of PR #1239 — out of scope for this verify-pass, which targets only the
diff actually shipped (the worktree janitor grace period).

## Disposition

This is a genuine contradiction of the shipped fix's stated purpose per this
ticket's escalation rule. Filed **wq#2942** — `[judgment] wq#2934 verify-pass
CONTRADICTED: prune_worktrees grace period keyed to commit age, not merge
age` — model `claude-opus-5`, effort `high`, archetype `rca-investigation`,
deps `2909,2934`, to rule on the right fix shape (candidates: key the grace
clock off the wq ticket's `done_at` for fworker worktrees, or off `git log -1
--format=%ct` on the merge commit itself rather than the branch tip, with
fail-closed semantics preserved for unregistered/non-fworker worktrees where
no `done_at` exists).
