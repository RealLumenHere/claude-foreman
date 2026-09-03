# wq#1393 — [repo-sweep] foreman: BEHIND_ONLY reconciliation

Automated sweep flagged `/home/lumen/foreman` `BEHIND_ONLY` (main ahead=0, behind=2 vs
`origin/main`). Per `harness/repo_reconcile.py`'s own `explain()` guidance for
`BEHIND_ONLY` (fetch + `git merge --ff-only`, not the tool's job) and the wq#1279/wq#1389
precedent (`/home/lumen/kb/ISSUE-LOG.md`) for protecting untracked files across the
fast-forward:

1. `git -C /home/lumen/foreman status` confirmed the flagged state plus two untracked
   files (`ops/wq1184-gatus-health-checks/deadman_state.json`, `plans/wq1270.md`) that
   were not part of the incoming 2 commits (`git -C /home/lumen/foreman diff --name-only
   main origin/main` touched only `evidence.json`,
   `harness/tests/test_harness_preflight.py`, and
   `reports/wq1389_repo_sweep_foreman_behind_only.md` — no collision risk).
2. `git -C /home/lumen/foreman stash push -u -m wq1393-preserve-untracked` — set the two
   untracked files aside, captured the stash SHA
   (`bd0561ed4b8ad50c93d6961c7f72bfd94d70bb29`) rather than relying on `stash@{0}`, since
   the stash stack is shared across worktrees/sessions.
3. `git -C /home/lumen/foreman fetch origin main && git -C /home/lumen/foreman merge
   --ff-only origin/main` — fast-forwarded `dc287e2..f08b737` cleanly (3 files changed, no
   conflicts).
4. `git -C /home/lumen/foreman stash apply bd0561ed4b8ad50c93d6961c7f72bfd94d70bb29`
   restored both untracked files byte-for-byte, then `git -C /home/lumen/foreman stash
   drop stash@{0}` (re-resolved by the unique tag before dropping) removed the now-
   redundant entry.
5. Verified: `python3 harness/repo_reconcile.py status --repo /home/lumen/foreman` →
   `IN_SYNC /home/lumen/foreman main ahead=0 behind=0`, exit 0.

No code change was required in `/home/lumen/foreman` itself — the repo's `BEHIND_ONLY`
verdict is a live-tree/origin divergence, not a defect in `harness/repo_reconcile.py`
(`harness/repo_reconcile.py:31` documents that `BEHIND_ONLY` deliberately stops short of
performing the fast-forward itself, since a fast-forward can rewrite files in a live
tree and untracked-file collisions are the caller's responsibility). This report is the
record of that manual reconciliation.
