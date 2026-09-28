# wq#2942 — judgment: wq#2934's contradiction of wq#2909 is upheld, and fixed

Judge: claude-opus-5 @ high, worktree `wq2942-a1`.
Subject: `ops/wq2620-prune-stale-worktrees/prune_worktrees.py` as landed by
wq#2909 (PR #1239).
Escalated by: wq#2934 (`reports/2934_verify_wq2909_grace_period_commit_vs_merge_age.md`).

## Ruling: CONTRADICTION UPHELD — fixed here, wq#2909 not reverted

wq#2934 is right on the facts and right to escalate. `merge_age_days()` read the
branch tip's own committer date, and **a commit date is only a lower bound on
landing time**. The gap is not a rounding error: it is unbounded, and it points
the wrong way. A branch committed long before it merged reads as *old*, so the
grace period that was supposed to protect it instead expires before the merge
even happens. That is the same class of harm wq#2909 was filed to prevent — a
merged+clean worktree deleted with no revert window — reached by a different
route.

wq#2909 is not reverted: its diff was directionally correct (a grace period is
the right control, `is_merged`-only pruning was genuinely unsafe) and every other
check it shipped stands. What it got wrong is which clock the control reads.

## The right fix shape

The ticket named two candidates and asked for a ruling. **Both, in one
precedence, taking the latest evidence** — because the sources cover
non-overlapping blind spots, and the safe direction is always the *later*
timestamp (smallest age, most protection). `landing_age_days()` now collects
every timestamp it can evidence and takes the max:

1. **The landing commit's date** — `git rev-list --ancestry-path
   <branch>..origin/<default>`, last line: the oldest commit that both descends
   from the branch tip and is reachable from the default branch. Git's own record
   of the merge event. This is the canonical recipe (the same one `git-when-merged`
   uses); its behaviour was verified empirically rather than assumed, across four
   landing shapes:

   | landing shape | `--ancestry-path` last line | age reported |
   |---|---|---|
   | `--no-ff` merge, nothing after | the merge commit | 0.00d |
   | `--no-ff` merge, later main commit | the merge commit | 0.00d |
   | fast-forward, later main commit | that later main commit | 0.00d |
   | fast-forward, branch **is** main's tip | *none — empty range* | — |

2. **`tasks.done_at`** for a `wqNNNN-aN` fworker worktree — the fleet's own
   record of when the ticket closed. This is what covers row 4 above, the one
   case git cannot date at all: a fast-forward writes no commit of its own, so if
   main has not moved since, nothing in the repository says when the branch got
   there.

3. **The branch tip's commit date** — wq#2909's clock, demoted to the floor it
   always was. Being `<=` both of the above it never wins the max; it is only
   what remains for a branch that never diverged, where there was no landing
   event to date in the first place.

Nothing readable at all ⇒ `(None, why)` ⇒ skip, fail closed, reason string says
so. An unreadable wq db makes source 2 contribute nothing rather than failing
closed there, because `wq_claim_is_live` already treats that exact condition as a
live claim before `classify` can return prune — fail-closed is preserved, just
not duplicated.

### Residual gap, stated rather than hidden

A **fast-forward landing whose branch tip is still the default branch's tip, in a
non-fworker worktree** (no `done_at`) falls back to the tip date and keeps the old
behaviour. Git records nothing else there, and the alternatives are worse: the
`origin/<default>` reflog would read "clone time" in a fresh clone and freeze the
tool into a permanent no-op. Documented in the README and demonstrated below, not
papered over.

## Evidence

**1. The wq#2934 reproducer flips.** `reports/wq2934_probe_grace_period_commit_vs_merge_age.py`,
unchanged in scenario, kept as the regression check (it still exits 1 against the
tip-commit clock):

```
branch merged into origin/main just now: True
tip-commit clock (wq#2909, the bug): 10.00 days
landing clock (wq#2942, the fix):    0.00 days, per landing commit d18cc459
classify() action='skip' reason='merged into origin/main, only 0.0d since landing per landing commit d18cc459 (grace period 7d)'
UPHELD: grace period held.
```

**2. Before/after, same scenario, both modules side by side** (pre-fix module
extracted from `HEAD`, so this could have shown no difference and thereby proved
the new tests vacuous):

```
--no-ff merge (forge default)
  pre-fix  (HEAD)     : prune — merged into origin/main, clean, no such ticket in wq db
  post-fix (wq#2942)  : skip  — merged into origin/main, only 0.0d since landing per landing commit 4d96bb7e (grace period 7d)

fast-forward, branch still at main's tip
  pre-fix  (HEAD)     : prune — merged into origin/main, clean, no such ticket in wq db
  post-fix (wq#2942)  : prune — merged into origin/main, clean, no such ticket in wq db
```

The second row is the residual gap above, shown failing on purpose.

**3. Suite: 20 → 26 tests, `26 passed`.** The two grace-period tests are rebuilt
on real git dates and real ancestry — wq#2934's fourth finding was that the old
"past grace period" test reached 8 days by monkeypatching `merge_age_days`, so no
git ancestry ever ran. New coverage, each able to fail:

- `test_late_landing_gets_the_full_grace_period` — the wq#2934 regression, and it
  asserts `landing commit` appears in the reason, so a right answer from the
  wrong clock still fails.
- `test_late_landing_protects_a_non_fworker_scratch_worktree` — **out of the
  population the fix was designed around** (wq#2630): a `lumen-pr` scratch
  worktree whose name cannot yield a ticket id, so `done_at` is unavailable by
  construction and the landing-commit clock must carry it alone.
- `test_fast_forward_landing_falls_back_to_wq_done_at` and
  `..._with_an_old_done_at_is_prunable` — the git-blind case, protected when the
  ticket just closed and **pruned** when it closed 10 days ago. The second exists
  because without it a fix that simply never prunes would pass the first.
- `test_done_at_wins_over_an_older_landing_commit` — reopened ticket: landed 10d
  ago, closed 5 minutes ago, latest evidence wins.
- `test_landing_age_diverges_from_tip_commit_age` — the two clocks on one branch,
  10.0d vs ~0d.
- `test_unreadable_commit_date_fails_closed` — now reaches the real fail-closed
  branch by breaking the commit-date read, instead of stubbing the age function.

**4. Production dry run, both roots, 2026-09-28.** No reclaim regression: the fix
is behaviour-neutral on today's live fleet and protective only on the divergent
shape.

```
post-fix: dry-run: scanned=1670 prunable=0 skipped=1670 size=0B
pre-fix : dry-run: scanned=1672 prunable=0 skipped=1672 size=0B
```

(Scan counts differ by 2 because other lanes create and land worktrees during the
sweep.) Of 1670, only 58 reach the grace check at all — 810 are dirty, 798 have
commits not on the default branch — and all 58 are inside the window, **31 dated
by a landing commit and 27 by `done_at`**. Those 27 are cases where `done_at` was
later than the landing commit, i.e. where source 2 is doing real work rather than
being a theoretical fallback.

## Follow-up: none filed

The fast-forward-at-tip gap is inherent to git, documented, and already covered
for the fworker worktrees that make up the population under
`/home/lumen/worktrees`. Filing a ticket for it would be filing a ticket against
git's data model.
