# wq#1312 — Adversarial verify-pass of wq#929 (primary-tree-divergence incident, PR#221)

**Verdict: CONTRADICT** — the code fix in PR#221 (merged as `91425b3`) is correct and its
own tests pass, but it is not deployed to the enforcement point that matters. The primary
tree at `/home/lumen/foreman` — whose `harness/guard.sh` is the literal script the
PreToolUse hook executes for every session, not a per-worktree copy — is still running the
pre-fix guard.sh. The exact vulnerability wq#929 was opened to close (an unreviewed `git
commit` landing directly on a primary's `main`, unblocked by guard.sh) is live right now.

## What was shipped (PR#221, commit 91425b3, merged to origin/main)

`harness/guard.sh`: `GIT_MOVE` regex extended from
`checkout|switch|restore|reset|rebase|pull` to also include
`commit|cherry-pick|revert|am`. `harness/tests/test_guard.py`: 7 new must-block cases
(`git commit`, `git commit -am`, `git -C <primary> commit`, a `cd <primary> && ... commit`
chain, `cherry-pick`, `revert`, `am`) + 3 new must-allow cases (commit inside the worker's
own worktree, `lumen-pr commit`, read-only `git log`). Plus an unrelated-but-ticket-scoped
cleanup: dead `lpp` dispatcher lane dropped, `nest` lane added to `dispatcher/config.json`
(wq#1280 stranded-ticket fix), `orchestrator/router.py` / `dispatcher/DESIGN.md` comments
updated to match.

## Probe: the merged fix is correct in isolation

Extracted `guard.sh` + `test_guard.py` + `README.md` at `91425b3` into a scratch dir and
ran them standalone (not against the primary's live, older copies):

```
$ python3 -m pytest .../tests/test_guard.py -q
................................                                         [100%]
32 passed in 12.29s
```

All 32 tests pass, including the 10 new wq#929 cases. The regex change, the block-message
wording update, and the test additions are internally consistent and do what the ticket
claims.

## The contradiction: merged to origin/main ≠ deployed to primary

- `git -C /home/lumen/foreman log -1 --format="%H %cI" main` → `1df2a21… 2026-09-02T15:25:01Z`
  — three minutes *before* `91425b3` (15:28:11Z). `main` does not contain the fix commit.
- `git -C /home/lumen/foreman status` → `"Your branch is behind 'origin/main' by 10
  commits, and can be fast-forwarded"` (growing since the merge — 8 other PRs have landed
  on top since).
- `grep -n 'commit|cherry-pick|revert|am' /home/lumen/foreman/harness/guard.sh` → no match.
  The deployed `GIT_MOVE` regex is still the old
  `checkout|switch|restore|reset|rebase|pull` — it does not catch `git commit`.
- This is the actual script in force: a Read attempt on an out-of-scope path in this
  session was blocked by the harness with the hook identified as
  `[/home/lumen/foreman/harness/guard.sh]` — the same physical file is the enforcement
  point regardless of which worktree a session runs from.
- No automated mechanism advances a primary onto `origin/main`. Checked:
  - All 131 active systemd timers (`systemctl list-timers`) — only
    `primary-tree-guard.timer` (every ~5 min) touches primary trees at all.
  - `harness/primary_tree_guard.py` (that timer's script) — `check_tree`/`restore_tree`
    only assert/restore which *branch* is checked out (wq#900 concern); it never fetches
    or merges new commits.
  - `harness/repo_reconcile.py` — `reconcile(apply=True)` only acts on `SAFE_RESET` /
    `REDUNDANT_AHEAD` verdicts; a plain `BEHIND_ONLY` primary (this repo's current state)
    is left untouched — `explain()` just prints the manual
    `git merge --ff-only origin/main` instruction.
  - `harness/repo_sweep.py` — files a ticket for any non-`IN_SYNC` repo; never applies a
    merge itself.
  - `/home/lumen/bin/sync-live` (the tool guard.sh's own block message points a worker to)
    is not on this session's Bash allowlist, so I could not check whether *it* is normally
    invoked on a schedule — but no timer names it, and nothing else does either.

So the fast-forward from a landed PR to a live, enforcing primary tree is a manual,
undocumented, unscheduled step. For wq#929 specifically, nobody has performed it since
`91425b3` merged: nothing prevents a session from running `git commit` directly inside
`/home/lumen/foreman` right now, which is the identical failure mode that produced
`2719f89` and opened this incident ticket in the first place.

## Other checks — no issues found

- `2719f89` (the original unreviewed commit) is confirmed an ancestor of `origin/main`
  (`git merge-base --is-ancestor 2719f89 origin/main` → success) — the "laundered in by ~20
  PR branches" claim in wq#929's done notes checks out and needed no destructive reset.
- The dispatcher lane cleanup (`lpp` dropped, `nest` added) matches the ticket's stated
  scope and cross-references (wq#1280) correctly; not scope creep.

## Disposition

wq#929's "Fixed: GIT_MOVE now also denies commit|cherry-pick|revert|am" claim is true only
at the git-history level (origin/main). Operationally the guard is not yet in effect
anywhere it runs from a primary tree, because merging to origin/main was never wired to
imply deploying to the primary. Recommend: (a) run the sanctioned
`git merge --ff-only origin/main` (or `sync-live`) in `/home/lumen/foreman` now to close
the live window, and (b) a follow-up ticket for the actual gap this incident chain never
addressed — no scheduled/triggered mechanism keeps *any* primary tree's `guard.sh` current
with `origin/main`, so this same "fixed in history, not in force" gap will recur for every
future guard.sh hardening until something other than an ad hoc manual merge closes it.
