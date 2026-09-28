# wq#2961: ffprimary blocked — wq#2908 branch is safe, the tree is not

## Summary

wq#2961 asked to fast-forward the lumen-bin primary (`/home/lumen/bin`) to `main` once the
`fix/wq2908-github-mirror-sync` branch was finished and the tree clean, so wq#2943's
ops-deploy gate (lumen-bin PR #167, `82ce01e`) goes live in the executed `wq` binary.

The wq#2908 premise checks out. The tree does not. **Not landed here** — blocked, filed as
wq#2973 (deps on wq#2961), ISSUE-LOG row 952.

## What I verified

1. `fix/wq2908-github-mirror-sync`'s tip (`c1b4dff`) is the exact merge-base with `main` —
   a strict ancestor with zero unique commits (`git -C /home/lumen/bin log --oneline
   main..fix/wq2908-github-mirror-sync` is empty). wq#2908 (status `done`, PR #166 merged
   at `2b78fc3`) and wq#2943 (PR #167, `82ce01e`) are both already ancestors of `main`. The
   branch itself is finished and safe to move past.

2. `git -C /home/lumen/bin status --porcelain` is **not** empty, and none of the dirt is
   wq#2908's:
   - 5 tracked-modified files (`litellm-budget-alert`, `litellm-provision-keys`,
     `rotate-provider-keys`, `tests/test_rotate_provider_keys_swap.py`,
     `vw-migrate-secrets`) are already byte-identical to `main`'s committed version —
     harmless, `lumen-pr`'s own `drop_identical_overlap` clears these.
   - 2 tracked-modified files are **not** harmless: `lumen-contradiction-scan` and
     `session-handoff` carry real uncommitted diffs that `main` does not have. Diffing
     each against `main` (`git -C /home/lumen/bin diff main -- <file>`) shows genuine
     content, not noise.
   - 9 untracked files, all `mtime` 2026-09-28, one of which (`cred-consumers`) appeared
     between two `git status --porcelain` calls run seconds apart during this
     investigation — the tree is being written to *right now*, not just carrying stale
     drift.

3. The 2 real tracked diffs trace to already-`done` tickets, hand-deployed straight to the
   live script and never landed in lumen-bin history:
   - `lumen-contradiction-scan`'s diff (the `open_subjects_by_value` dead-value dedup) is
     wq#2850 (done, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1188, merged
     `77c6cbefc6`). It matches `foreman/ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py:334,340,377`
     verbatim — recoverable by reinstalling that ops bundle if ever lost.
   - `session-handoff`'s diff (the wq#2759 comment folding ISSUE-LOG.md into the timer's
     own commit scope) is wq#2759 (done). It has **no other backing copy**:
     `foreman/ops/wq1630-handoff-merge/session-handoff` is a stale pre-wq#2759 snapshot
     (`grep -c wq#2759` on it is 0). Discarding this file's live state would silently
     regress a landed fix with no way to recover it.

4. Because of (2c) and (3), neither sanctioned resync path is safe to run right now:
   `sync-live` (`/home/lumen/bin/sync-live`) refuses unconditionally on any dirty tree, and
   `lumen-pr ffprimary`'s own first gate (`cur != DEFAULT_BRANCH`, `lumen-pr:635`) requires
   an actual branch checkout before its overlap check even runs — and a worker's raw
   `git checkout` against a PRIMARY tree is denied by `harness/guard.sh`'s wq#900 wall
   regardless of whether the checkout would touch these 2 files. Discarding the 2 real
   diffs to force a clean tree would regress wq#2759 live with no recovery path; doing
   anything at all while a 9th untracked file is still appearing mid-investigation risks
   clobbering whatever is writing it.

## What I did instead

- ISSUE-LOG.md row 952: full finding, cross-referenced to row 920 (wq#2908's own
  discovery of this worker/primary-tree gap) and the wq#1653 precedent
  (`lumen-bin@c3bf2e9`, "rescue live-tree drift from the primary checkout") for the
  guard-compliant rescue shape — a scratch `lumen-bin` worktree under the ticket's own
  `/tmp` grant (`git worktree add` is not one of guard.sh's walled verbs), landed via
  `lumen-pr`, before any discard or resync is attempted.
- Filed wq#2973 (`deps=2961`) to do that rescue, triage the 9 untracked files, confirm the
  tree holds steady at zero porcelain output, and then run the actual `ffprimary`/verify
  steps this ticket specified (`grep -c wq#2943 /home/lumen/bin/wq`,
  `pytest tests/test_wq2943_ops_deploy_url_gate.py`).
- Left `/home/lumen/bin` untouched — no discard, no worktree add, no resync attempt —
  given evidence of a live concurrent writer.

## Still open

wq#2961 stays open, blocked on wq#2973. Once wq#2973 lands and the primary is clean and
quiet, re-run this ticket's own verification commands against the live tree.
