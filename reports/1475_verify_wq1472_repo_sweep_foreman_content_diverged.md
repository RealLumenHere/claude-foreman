# wq#1475 — verify-pass: wq#1472 [repo-sweep] foreman: CONTENT_DIVERGED

**Verdict: CONFIRM** — the reconciliation is real and verified live (`IN_SYNC`,
no data loss, untracked files intact, claimed test counts match exactly), and
the one substantive risk in the shipped diff was already found, precisely
diagnosed, and correctly deferred by the author as its own decision-requiring
ticket (wq#1473) rather than silently left in place.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/339 (merged,
`7250cab` "split frank queue into typed review lanes", landed as `1bfabfd`),
companion PR #340 (`f7512a6`, the `PR_PENDING` harness fix that unblocked
landing it).

## What was verified

1. **Repo is actually reconciled, live.** `python3 harness/repo_reconcile.py
   status --repo /home/lumen/foreman --no-fetch` →
   `IN_SYNC /home/lumen/foreman main ahead=0 behind=0` at `1bfabfd`. Confirmed
   independently via `git -C /home/lumen/foreman log --oneline` on both
   `main` and `origin/main` (identical tips).

2. **No content was lost in the rewind.** Fetched `refs/pull/339/head` into
   this worktree and diffed `9903f44..7250cab` (the PR) against
   `fd5c90e..1bfabfd` (what actually landed): both touch only
   `dispatcher/foreman_dispatcher.py`, same one-line `HUMAN_LANES` change,
   **same resulting blob** (`0f25c12`). The PR_PENDING rewind that made this
   landable did not alter or drop the diverged commit's content.

3. **The 5 untracked files claim holds.** `git -C /home/lumen/foreman status
   --porcelain` still shows exactly 5 untracked files
   (`deadman_state.json`, `plans/wq1270.md`, `wq1421.md`, `wq1424.md`,
   `wq1435.md`) — matches the done-notes claim, nothing was clobbered by the
   reconcile.

4. **Test counts match exactly.** Claimed "28 tests pass (11 pre-existing +
   4 new + 13 sweep)". Ran live: `harness/tests/test_repo_reconcile.py` →
   **15 passed**; `harness/tests/test_repo_sweep.py` → **13 passed**.
   15 + 13 = 28. Exact match, not rounded.

## The one real risk — already caught by the author, not by me

Read `dispatcher/foreman_dispatcher.py` cold and tried to break the shipped
`HUMAN_LANES = {"user", "user-projects", "frank-re", "frank-hw",
"frank-funding", "frank-cred"}` change: `scan_candidates()` (line 702) is the
*only* candidate source (line 1060) and iterates `cfg["lanes"]` — a lane
absent from `dispatcher/config.json` is never queried via `wq list`, so it
never reaches the `lane in HUMAN_LANES` check (line 1149) at all. Ran the
repo's own orphan-lane probe (`dispatch_config_lint.py check --dry-run`,
wq#845) to confirm live, not just by reading:

```
FAIL (5):
  lane='frank-cred' has 2 open ticket(s) in queue.db but no config.json entry
  lane='frank-funding' has 3 open ticket(s) in queue.db but no config.json entry
  lane='frank-hw' has 4 open ticket(s) in queue.db but no config.json entry
  lane='frank-re' has 3 open ticket(s) in queue.db but no config.json entry
  lane='user-projects' has 1 open ticket(s) in queue.db but no config.json entry
```

All four new lanes (plus the pre-existing `user-projects` entry) are inert:
tickets sitting in them right now (confirmed via `wq list frank-re` etc. —
these are real, open, non-hypothetical tickets) are invisible to the
dispatcher, not reclassified `frank_gated` as the commit message claims.

This is exactly the finding I was about to write up as a CONTRADICT — until
checking wq#1472's own done notes, which already state it verbatim:

> Follow-ups: wq#1473 (the landed HUMAN_LANES entries are inert —
> frank-*/user-projects missing from dispatcher/config.json, so
> scan_candidates never reaches them)

wq#1473 (open, unclaimed) restates the mechanism with the same line numbers
and adds a detail I hadn't chased: `lane_cap` is checked *before* the
`HUMAN_LANES` branch (line 1145), so a low `max_per_day` would masquerade as
`lane_cap` rather than `frank_gated` if these lanes are ever registered. It
correctly frames the fix as a decision (register the lanes and accept
~17+ new `frank_gated` skips per pass moving dashboard numbers, vs. drop the
five inert `HUMAN_LANES` entries so the set stops implying coverage it
doesn't have) rather than a mechanical patch — right call, not something a
repo-sweep reconciliation ticket should have folded in un-reviewed.

## Disposition

No contradiction. wq#1472 did what a CONTENT_DIVERGED repo-sweep ticket is
for — reconciled the git state without losing work, landed via a real PR
through a genuine tooling deadlock (ISSUE-LOG #217/#218) — and it disclosed
the one open question in the content it happened to be carrying instead of
burying it. Confirming on the board.
