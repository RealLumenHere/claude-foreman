# wq#1495 — verify-pass of wq#1494 ([repo-sweep] foreman: PR_PENDING)

**Verdict: CONFIRMED.** wq#1494 was not a code PR but a direct operational fix: run
`repo_reconcile.py reconcile --apply` on `/home/lumen/foreman` to resolve a PR_PENDING
sweep alert. Every claim in the ticket's `done` notes checks out against the live repo
state, and the exit-2/ISSUE-LOG#223 diagnosis is correct when read against the tool's
source.

## What was claimed (wq#1494 notes)

- `repo_reconcile.py reconcile --apply` archived 3 unpushed commits as tag
  `archive/reconcile-main-20260908T061842Z`, reset `main` to merge-base `3048094`, now
  IN_SYNC with `origin/main`.
- The tool's PR_PENDING post-condition assert expected `BEHIND_ONLY` but got `IN_SYNC`
  (because `origin/main` had nothing past the merge-base) — exit 2, but the git mutations
  had already completed correctly before the assert fired.
- Content preserved on `origin/test/wq1468-lumen-bin-single-host-endpoint-constants-a4`
  plus the archive tag.
- ISSUE-LOG #223 flags a followup: `repo_reconcile.py:321` should accept IN_SYNC as well
  as BEHIND_ONLY for the PR_PENDING branch.

## Independent verification

- `/home/lumen/foreman` HEAD == `origin/main` == `3048094c02857...` — genuinely IN_SYNC,
  not just claimed. `python3 harness/repo_reconcile.py status --repo /home/lumen/foreman`
  (the tool's own probe) independently reports `IN_SYNC ... ahead=0 behind=0`.
- `git log 3048094..archive/reconcile-main-20260908T061842Z` shows exactly 3 commits
  (2 merge commits + `f4645f3` fire-mode work); `git diff 3048094
  archive/reconcile-main-20260908T061842Z --stat` touches exactly the 3 files the notes
  name: `harness/fire_gate.py`, `harness/fworker.py`,
  `harness/tests/test_wq1487_falsealarm.py`. Matches the notes exactly, no discrepancy.
- Fetched `origin/test/wq1468-lumen-bin-single-host-endpoint-constants-a4` and diffed it
  against the archive tag at those same 3 paths: empty diff, byte-identical. The
  "content preserved on the PR branch" claim is not just asserted, it's true.
- Working tree at those 3 paths is clean (`git diff HEAD` empty for the 2 surviving files;
  `test_wq1487_falsealarm.py` correctly absent — it didn't exist at the merge-base, and the
  tool's `restore --source` step deletes rather than refusing for paths the target ref
  never had, per the code's own comment at `repo_reconcile.py:313-317`). No staged or
  stray content left behind.
- Read `repo_reconcile.py:264-326` (the `reconcile()` function) directly: for the
  PR_PENDING branch (line 293-299) `target/expected` is unconditionally `(st["base"],
  BEHIND_ONLY)` — there is no branch for "origin already sits at the merge-base." The
  post-reset assert at line 320-322 (`after["verdict"] != expected → raise GitError`)
  therefore fires whenever a PR_PENDING repo's remote hasn't moved since the PR branched,
  exactly the scenario this run hit. The ISSUE-LOG #223 diagnosis is accurate, not a
  guess dressed up as root cause.
- Confirmed the gap is real and untested: `harness/tests/test_repo_reconcile.py`'s
  PR_PENDING tests (`test_status_reports_pr_pending_when_the_work_is_pushed_on_a_pr_branch`,
  `test_pr_pending_apply_rewinds_and_leaves_the_fast_forward_runnable`, etc.) all set up
  scenarios where the remote is ahead too (e.g. `ahead=1 behind=3`); none cover a PR_PENDING
  repo whose remote sits exactly at the merge-base. The followup is a genuine, currently
  uncovered edge case, not fixed in this ticket — correct scoping for a sweep-response
  ticket to log rather than silently patch.
- Ran the tool's full test suite as the probe: `python3 -m pytest
  harness/tests/test_repo_reconcile.py -q` → **18 passed**, confirming wq#1494's operation
  didn't regress the reconcile tool itself.
- `wq show 1494` — `status: done`, notes match observed repo state verbatim (tag name,
  base SHA, touched paths, preservation ref).

## Adversarial checks that did NOT find a break

- Checked whether the reset could have silently dropped content that isn't on the archive
  tag or the PR branch: both independently carry identical bytes at all 3 touched paths
  (verified above), so nothing is unrecoverable even though the working tree no longer has
  it.
- Checked for leftover untracked/staged cruft from the operation: the 4 untracked files
  present in `git status` (`plans/wq1270.md`, `plans/wq1421.md`, `plans/wq1435.md`,
  `ops/wq1184-gatus-health-checks/deadman_state.json`) all predate wq#1494's run window
  (2026-09-08 06:18–06:20) by hours to days — pre-existing cruft, not a side effect of this
  reconcile.
- Checked whether exit code 2 actually indicates the operation failed despite the notes'
  claim otherwise: `EXIT = {..., ERROR: 2}` in the tool's exit-code table (line 102-103) is
  shared between genuine errors and this specific assert; re-running `status` now (not
  trusting the historical exit code) independently confirms IN_SYNC, so the notes'
  "mutations completed correctly despite the exit 2" claim is verified, not just asserted.

## Conclusion

No contradiction found. The repo is in the exact state the ticket claims, the archived and
preserved content is byte-identical at every touched path, the root-cause diagnosis for the
exit-2 quirk holds up against the source, and the reconcile tool's own test suite is
unaffected. Board stays CONFIRMED — no escalation to opus-5@high.
