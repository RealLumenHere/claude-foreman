# wq#1389 — [repo-sweep] foreman: BEHIND_ONLY reconciliation

Automated sweep flagged `/home/lumen/foreman` `BEHIND_ONLY` (main ahead=0, behind=20 vs
`origin/main`). Per `harness/repo_reconcile.py`'s own `explain()` guidance for
`BEHIND_ONLY` (fetch + `git merge --ff-only`, not the tool's job) and the wq#1279
precedent (`/home/lumen/kb/ISSUE-LOG.md` #66) for protecting untracked files across the
fast-forward:

1. `git -C /home/lumen/foreman status` confirmed the flagged state plus two untracked
   files (`ops/wq1184-gatus-health-checks/deadman_state.json`, `plans/wq1270.md`) that
   were not part of the incoming 20 commits (`git log --oneline main..origin/main -- <paths>`
   returned nothing — no collision risk).
2. `git -C /home/lumen/foreman stash push -u -m wq1389-a1-preff-<ts>` — set the two
   untracked files aside, captured the stash SHA
   (`4796b0eccf7a2e98507934b1f56ad1004022b0a3`) rather than relying on `stash@{0}`, since
   the stash stack is shared across worktrees/sessions.
3. `git -C /home/lumen/foreman fetch origin && git -C /home/lumen/foreman merge --ff-only
   origin/main` — fast-forwarded `f75b2b8..dc287e2` cleanly (22 files changed, no
   conflicts).
4. `git -C /home/lumen/foreman stash apply 4796b0ecc...` restored both untracked files
   byte-for-byte, then `git -C /home/lumen/foreman stash drop stash@{0}` (re-resolved by
   the unique tag before dropping) removed the now-redundant entry.
5. Verified: `python3 harness/repo_reconcile.py status --repo /home/lumen/foreman` →
   `IN_SYNC /home/lumen/foreman main ahead=0 behind=0`, exit 0.

No code change was required in `/home/lumen/foreman` itself — the repo's `BEHIND_ONLY`
verdict is a live-tree/origin divergence, not a defect in `harness/repo_reconcile.py`
(`harness/repo_reconcile.py:31` documents that `BEHIND_ONLY` deliberately stops short of
performing the fast-forward itself, since a fast-forward can rewrite files in a live
tree and untracked-file collisions are the caller's responsibility). This report is the
record of that manual reconciliation.
