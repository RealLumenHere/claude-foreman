# wq#2840 — Adversarial verify-pass of wq#2829 (repo-sweep inventory: BEHIND_ONLY)

**Verdict: CONFIRM.** wq#2829's reconciliation of `/home/lumen/fleet/inventory` (fetch +
`git merge --ff-only origin/main`, `76bdaa8` -> `751d218`) is real, timely, and correctly
evidenced. No defect found.

## What was checked

- **Live repo state** — `git -C /home/lumen/fleet/inventory status` / `rev-parse HEAD`:
  clean tree, `HEAD` tracking `origin/main`. `python3 harness/repo_reconcile.py status
  --repo /home/lumen/fleet/inventory` independently reported `IN_SYNC ... ahead=0 behind=0`
  (this is repo-sweep's own re-runnable probe, not the ticket author's claim taken on
  faith — per wq#2630 it can fail: a still-behind or diverged tree would have reported
  differently). Note: between my first and second check, an unrelated commit (`af0c153`,
  wq#2812's nephthys fix) landed on `origin/main` and fast-forwarded further — expected
  churn on a live, actively-swept repo, not a regression in wq#2829's fix.
- **Authenticity of the claimed action** — `git -C /home/lumen/fleet/inventory reflog show
  --date=iso main` shows `751d218 main@{2026-09-28 03:11:54 +0000}: merge origin/main:
  Fast-forward`, 37s after the ticket's `claimed_at` (03:11:17) and within the `done_at`
  window (03:17:14). This is an immutable, timestamped record that a real ff-merge
  happened when claimed, not a pre-existing state the ticket took credit for. Tree was
  clean at the time (no untracked-file stash was needed, matching the notes).
- **The AC1 citation** — notes claim `{"citation": "fleet-inv:394-433", "repo":
  "/home/lumen/fleet/inventory", "rev": "751d218..."}` via `_crossrepo_citation`
  (wq#2753). Reran `python3 harness/fworker.py verify-evidence --ticket 2829 --worktree
  wq2829-a1` fresh: `{"ok": true, ...}`. Independently confirmed the mechanics
  `_crossrepo_citation` checks: `751d218` is an ancestor of `origin/main`; `git diff-tree
  --no-commit-id --name-only -r 751d218` touches `fleet-inv` (and
  `tests/test_drift_coverage.py`); `git show 751d218:fleet-inv` has >433 lines and
  lines 394-433 are the actual hardware.ram/storage drift-check logic wq#2758 added
  (matches the commit's own subject). The citation is grounded, not fabricated.
- **Why the plain (non-cross-repo) citation path could never have worked** — the ticket's
  own worktree branch `feat/wq2829-repo-sweep-inventory-behind-only-a1` never moved past
  `76bdaa8` (the pre-fix commit); `76bdaa8` is itself an ancestor of `origin/main`, so
  `merge-base(76bdaa8, origin/main) == 76bdaa8`, making the branch's own diff against that
  merge-base empty by construction. A BEHIND_ONLY fix lands on the *primary* tree
  directly (ff-only merge), never on the ticket's tracking branch, so `_check_citation`'s
  ordinary in-worktree-diff path is structurally unsatisfiable for this ticket shape.
  ISSUE-LOG #864 already documents this and the cross-repo workaround; confirmed it's the
  correct call, not a gate that got quietly bypassed.
- **Scope of the ticket's own claim** — notes claim only the reconciliation + AC1 evidence,
  nothing else. No stray edits, no unrelated code shipped alongside the merge (unlike
  wq#1924's finding against wq#1923, there is no "content the diff also shipped" to
  separately audit here — the cited code at `fleet-inv:394-433` is wq#2758's own,
  already-landed commit, not new code wq#2829 authored).
- **Acceptance clause not independently verifiable from this sandbox**: "the next sweep
  ... resolves aq#2075 itself" — `aq_escalate`/repo-sweep read/write `alerts.db` at
  `/home/lumen/agents/alert-queue/alerts.db`, which is outside this verify worker's
  granted directories (only `agents/work-queue` is granted, not `agents/alert-queue`).
  Did not attempt to work around this boundary. The IN_SYNC state that would cause
  aq#2075 to self-clear on the next sweep pass is independently confirmed above; the
  clearing mechanism itself is pre-existing repo-sweep/aq_escalate logic, not something
  wq#2829 introduced.

## Disposition

No contradiction found. wq#2829's fix is genuine (reflog-timestamped), correctly scoped
(a bare ff-only merge, no incidental changes), and its evidence citation is real and
resolves under an independent rerun of the same gate. Closing wq#2840 as CONFIRM; no
escalation to opus-5@high.
