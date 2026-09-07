# wq#1465 — verify-pass: wq#1464 [repo-sweep] bin: BEHIND_ONLY

**Verdict: CONTRADICT** — the done notes cite `repo_reconcile.py status` reporting
`IN_SYNC` as proof of completion, but that check was only run *before* PR #68 was
merged. Re-running the exact same command now, live, returns `BEHIND_ONLY
/home/lumen/bin main ahead=0 behind=2` — the repo is in a worse drift state than
when the ticket was opened (behind=1). The PR's own merge is what caused it: it
landed two new commits on `origin/main` that the just-fast-forwarded local `main`
ref does not have, immediately re-triggering the exact condition the ticket
closed as solved.

PR: http://100.121.17.5:3000/lumen/lumen-bin/pulls/68 (merged, branch
`fix/wq1464-repo-sweep-log-clean`, commits `ad5d27f` + merge `49ddf28`).

## Reproduction

```
$ python3 harness/repo_reconcile.py status --repo /home/lumen/bin --no-fetch
BEHIND_ONLY /home/lumen/bin main ahead=0 behind=2
```

`git -C /home/lumen/bin log --oneline -5 origin/main`:

```
49ddf28 fix(bin): resolve BEHIND_ONLY repo-sweep drift      <- PR #68 merge commit
ad5d27f docs(bin): log wq#1464 ...                          <- PR #68 evidence commit
0015923 fix(fleet-keymesh): add sobek, read fleet pw from file
7129c40 fix(bin): resolve BEHIND_ONLY repo-sweep drift      <- PR #65 merge commit (wq#1449)
22b9d84 docs(bin): log wq#1449 ...                          <- PR #65 evidence commit (wq#1449)
```

Local `main` ref sits at `0015923` — exactly two commits behind, and those two
commits are `ad5d27f`/`49ddf28`, i.e. PR #68 itself.

## What wq#1464 got right

- Correctly diagnosed that the dispatched worktree's branch was forked from
  another lane's dirty tip (`feat/wq1374-...@96717f2`, not `main`) and would have
  bundled wq#1374's unlanded WIP into any PR built on it — same trap ISSUE-LOG
  #203 already documents, and the fix (cut a fresh branch off `origin/main`
  instead) matches exactly.
- Left the contaminated PR #67 open rather than force-pushing over it or merging
  foreign WIP into `main` — correct call, `main` never touched by the mistake.
- The `git fetch origin main:main` fast-forward of the local ref (no working tree
  impact, since `main` isn't checked out anywhere in this repo) was itself correct
  and *did* reach `IN_SYNC` at the moment it ran.
- The one-line `repo-sweep-log.md` addition is factually accurate and touches
  nothing else.

## The actual defect: the fix regenerates its own trigger

wq#1464's archetype is `standard-engineering`, which is not in
`harness/acceptance.py`'s `NON_DIFF_ARCHETYPES` (`verify-pass`,
`rca-investigation`, `fire-response`, ...). `acceptance.py:122-128` forces every
other archetype to satisfy a citation criterion — "cite file:line of the code
that implements it, in a file this branch actually touches" — before the ticket
can close. A pure `BEHIND_ONLY` reconciliation is a no-diff, ref-only operation
by design (`repo_reconcile.py:31`, "fast-forwardable again, which is all this
tool ever promises"), so satisfying the citation gate requires *inventing* a diff.
wq#1464 did that by opening PR #68 to append a line to `repo-sweep-log.md` and
merging it into `origin/main`.

That merge is the bug: landing any new commit on `origin/main` after the local
ref has been fast-forwarded to the old tip immediately puts local `main` behind
again by exactly those new commits. This is not hypothetical — it already
happened once before. wq#1449 (ISSUE-LOG #195) used the identical pattern
(fast-forward local ref, then land its own `repo-sweep-log.md` line via PR #65 /
merge commit `7129c40`), and that merge is precisely what caused *this* ticket
(wq#1464) to be dispatched in the first place — `7129c40`/`22b9d84` are two of
the commits that made `/home/lumen/bin` `BEHIND_ONLY` for wq#1464 to "fix." wq#1464
has now done the same thing to itself: PR #68's commits (`ad5d27f`/`49ddf28`) are
sitting on `origin/main` waiting for the next repo-sweep ticket to dispatch and
"resolve" by fast-forwarding past them and landing one more evidence commit that
will trigger the one after that.

This is a self-perpetuating loop, confirmed live across two full cycles
(wq#1449 -> wq#1464 -> next), not a one-off mistake by this author. The fix isn't
in the diff wq#1464 shipped — it's that the acceptance-gate citation requirement
is incompatible with a ticket class whose correct remediation is diff-free.

## Disposition

Fix shape is a judgment call — e.g. add repo-sweep reconciliation tickets to
`NON_DIFF_ARCHETYPES` so the citation gate stops demanding a diff for a no-diff
fix, or stop landing the evidence line via a PR-merge into the swept repo's
tracked `main` at all (record it in the wq ticket/kb instead) — not mine to make
here per verify-pass precedent (wq#872/#1268, wq#1433/#1434, wq#1459). Not filing
the fix myself. Escalating to opus-5@high.
