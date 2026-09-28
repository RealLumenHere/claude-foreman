# wq#2960 — verify-pass of wq#2942 (PR #1257): CONFIRMED

Verifier: claude-sonnet-5 @ high, fresh instance, worktree `wq2960-a1`.
Subject: `ops/wq2620-prune-stale-worktrees/prune_worktrees.py` as landed by wq#2942
(PR #1257, commit `698a4e2`), which fixed wq#2934's contradiction of wq#2909.

## Verdict: CONFIRMED — no contradiction

wq#2942's fix does what wq#2934 asked for and what its own judgment report
(`reports/2942_judgment_prune_grace_landing_clock.md`) and the README claim it
does. I read the diff cold, ran the existing regression probe, then tried to
break it on the one seam that looked underspecified — and did reproduce a
zero-grace prune, but it is the *same* residual gap wq#2942's own judge already
found, disclosed, and ruled acceptable, not a new defect the shipped fix failed
to address.

## What I checked

**1. Existing suite and probe, as shipped.**
`python3 -m pytest tests/test_prune_worktrees.py -q` → `26 passed`.
`python3 reports/wq2934_probe_grace_period_commit_vs_merge_age.py` →
`UPHELD: grace period held.` (landing clock reads the merge commit, 0.00d, not
the branch tip's 10.00d). Matches the judgment report's own evidence.

**2. Tried to break it on the population outside the fworker case** (wq#2630:
an acceptance probe must be able to fail, and must be drawn from outside the
population a fix was designed around). The fix's `done_at` fallback only fires
for `FWORKER_WT_RE` (`wqNNNN-aN`) names. I built a probe
(`reports/wq2960_probe_ff_scratch_worktree_grace_gap.py`) combining two things
the *existing* test suite exercises separately but never together:
- `test_late_landing_protects_a_non_fworker_scratch_worktree` — non-fworker
  scratch name, `--no-ff` merge.
- `test_fast_forward_landing_falls_back_to_wq_done_at` — fast-forward landing,
  fworker name.

The untested intersection — non-fworker scratch worktree, fast-forward landing,
branch tip still `origin/main`'s tip — reproduces wq#2934's original bug:

```
branch fast-forwarded into origin/main just now: True
tip-commit clock (wq#2909/wq#2934, the bug): 10.00 days
landing clock (wq#2942, the fix):            10.00 days, per branch tip commit, no landing event on record
classify() action='prune' reason='merged into origin/main, clean, no owner record'
```

**3. Checked whether this is actually new.** It is not. `prune_worktrees.py`'s
own `landing_age_days()` docstring, the README (lines 53-55, "Residual gap, by
construction: a fast-forward still at the default branch's tip, in a
*non*-fworker worktree (no `done_at`), falls back to the tip date. Git records
nothing else there."), and wq#2942's judgment report (row 4 of its evidence
table, "fast-forward, branch **is** main's tip" → age unset; its "Evidence #2"
before/after table showing this exact shape prunes both pre- and post-fix,
captioned "shown failing on purpose"; and its explicit "Follow-up: none filed"
section) all name this precise gap and rule it acceptable — inherent to git's
data model, since a fast-forward with nothing landed after it leaves no commit
anywhere dating the merge, and the only other write available for a scratch
worktree (`lumen-pr`'s `.owners/<name>` file, `agent=`/`created=` — checked in
`bin/lumen-pr:972`) records worktree *creation*, not landing, so it would
under-protect rather than fix anything.

**4. Checked whether the original ticket actually asked for more than this.**
wq#2934's ticket text (escalated as wq#2942) is explicit: "key the grace clock
off the wq ticket's `done_at` for fworker worktrees ... *or* off the merge
commit's own date ... **preserving fail-closed semantics for non-fworker/
unregistered worktrees with no done_at available**." That is not a demand to
close the gap for that population — it is a demand not to regress it. The
fix does not: for a non-fworker, fast-forward-at-tip worktree, behavior is
byte-identical to pre-fix (branch tip commit date, same as wq#2909 shipped).
Nothing got worse; the two populations wq#2934 named as fixable (fworker via
`done_at`, general merge-commit case via `landing_commit`) are fixed, each with
a passing, real-git-ancestry test.

**5. Added the missing `--verify` handler** (`ops/wq2620-prune-stale-worktrees/
install.sh`, per `ops/VERIFY_CONTRACT.md` — this bundle had none, which blocks
confirming per wq#1696/wq#1696's contract). Read-only: `vunit_file` on both
unit files, `venabled`/`vactive` on the timer, `vdone`. Ran it against this
worktree's copy (never the primary tree's un-guarded copy, which would fall
through an unrecognized `--verify` arg into a live `sudo systemctl enable
--now` — checked `ops/_verify.sh` and `bin/lumen-pr`/`harness/liveness.py:432
ops_verified()` first to confirm the invocation shape and the hazard before
running anything):

```
$ python3 -c "...subprocess.run(['bash', '.../wq2960-a1/ops/wq2620-prune-stale-worktrees/install.sh', '--verify'])..."
VERIFY OK
```

exit 0 — the timer and service are live on Lumen and byte-match what the
bundle ships.

## Committed evidence

- `reports/wq2960_probe_ff_scratch_worktree_grace_gap.py` — the new probe,
  pinned (throwaway repo built from scratch each run, no external state),
  kept as a regression check for the disclosed gap: it should keep exiting 1
  unless someone changes `landing_age_days()`'s fallback chain, at which point
  it becomes a real regression test rather than a documentation exercise.

## Liveness

`ops_verified(wq2620-prune-stale-worktrees)` → OK (see #5 above; the bundle
now has a handler for the daily `ops-deploy-drift` sweep and for future
`[verify]` tickets to cite instead of "no handler").
