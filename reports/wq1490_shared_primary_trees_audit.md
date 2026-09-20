# wq#1490 — shared primary trees: triple-duty audit and the byte-identical land guard

Date: 2026-09-20. Lane: ops. Companion code change: lumen-bin PR #114
(http://100.121.17.5:3000/lumen/lumen-bin/pulls/114, landed as `0d83cb0`; lumen-pr `live_blob`,
`stage_identical_overlap`, `drop_identical_overlap`, and the wq#1490 block in `dirty_target_guard`;
tests in `tests/test_lumen_pr_dirty_guard.py`). `lumen-pr land` fast-forwarded /home/lumen/bin to
`0d83cb0` itself, with four unrelated dirty paths left in place — the ticket's TEST clause for
"file appears on disk without manual git".

## What the ticket asked for, and where each item stood on 2026-09-20

| # | Deliverable | State found | Action |
|---|-------------|-------------|--------|
| 1 | land or stash-preserve wq#1374 in-flight work | Landed by that lane: `feat/wq1374-orch-operability-...` is 0 commits ahead of origin/main; its worktree at `/home/lumen/scratch/worktrees/bin-feat-wq1374-orch-operability` is clean | none needed |
| 2 | checkout main on /home/lumen/bin, ff to origin/main | Already on `main`, 0 ahead / 0 behind origin/main (`0145dbf`). Dirty: `lumen-contradiction-scan`, `storage-manifest-drift`, `wq523_pat_residue.py` (modified) + `minerva-presentation-stability` (untracked) — another live lane's work, left alone | verified timers: `power-remediate.timer` last fired 18:08:03Z, `fleet-auth-ssh-probe.timer` last fired 18:04:26Z (systemctl list-timers); `wq show 1490` ran. `fleet-keymesh --list` and `aq --help` are not on the worker allowlist and were not run |
| 3 | deploy-on-merge: canonical tree advances only by ff-pull after land; sessions edit in worktrees | Done by wq#1536: `lumen-pr land` calls `ff_primary_after_land` on both the linked-worktree path and the primary path; `lumen-pr start --worktree` creates under `/home/lumen/scratch/worktrees` (`WORKTREE_ROOT`) | none needed; this PR extends the ff so an identical uncommitted overlap no longer blocks it |
| 4 | wq#771 guard false positive on byte-identical dirty paths | wq#1474 covered the *unpushed-commit* half only (compares the tree's committed blob). The *uncommitted* half — PR#75's actual shape — still refused | fixed in this PR: the guard hashes the live working-tree file and drops the path when it equals the PR head blob; the post-land ff stages such paths instead of refusing |
| 5 | audit other shared primary trees | see below | listed |

## PR#75 (fix/wq1454-bin-consumers-inventory) — the live state the ticket said to preserve is gone

The ticket says `fleet-auth-ssh-probe.sh` and `power-remediate` were patched in place on 2026-09-08 and
must not be reverted before PR#75 merges. On 2026-09-20:

- `origin/fix/wq1454-bin-consumers-inventory` is still 1 commit ahead of origin/main (`ef31791`), i.e. PR#75 has not merged.
- `git -C /home/lumen/bin log origin/main -- fleet-auth-ssh-probe.sh power-remediate` stops at `946a526`: main never got the patch.
- The live files are byte-identical to their `.bak.20260908_054148Z` siblings and to HEAD, so the in-place patch was reverted when the tree was moved back to main by someone else.

The content survives only in PR#75's branch. That PR is the wq#1454 lane's to land; with this fix it would
no longer be refused for the shape that blocked it on 09-08, but it also no longer has a dirty twin on disk,
so a plain land now suffices.

## Triple-duty audit (canonical checkout + live running tree + scratch), 2026-09-20

| Tree | Branch | Dirty paths | main vs origin/main | Linked worktrees | Verdict |
|------|--------|-------------|---------------------|------------------|---------|
| /home/lumen/bin | main | 4 (another lane's) | 0 / 0 | 40+ (scratch/worktrees/bin-* and nested `worktrees/wqNNNN-*/.binwt`) | still triple duty: live edits land here directly (`.bak.20260908_*` siblings: dc-enforce-config.py, dc-patch-tripwire, aq_escalate.py, lumen-log-watch, wq). ff-on-land now works; in-place patches identical to a PR no longer block it |
| /home/lumen/foreman | main | 4 | 0 / 0 | ~1600 (`/home/lumen/worktrees/wqNNNN-aN`, never pruned) | same shape as bin: primary is the running tree (dispatcher, harness) and is edited live; ff-on-land in place since wq#1536. Worktree count itself is a hygiene item (stale worktrees are never removed) |
| /home/lumen/fleet/inventory | main | 0 | 0 / 0 | 7 | clean; ff-on-land in place. Least at risk |
| /home/lumen/sentinel | not a git repo (only `sentinel/config` is referenced by `harness/read_dir_sweep.py`) | n/a | n/a | n/a | not a shared checkout; nothing to reconcile |

Rule for all of them, now enforced by tooling rather than by memory: the primary advances only by
`lumen-pr land` (ff after merge) or `lumen-pr ffprimary <tree>`; a session edits in a worktree from
`lumen-pr start <repo> <type>/<slug> --worktree`. An in-place hotfix to a running script is allowed
but must be carried by a PR with the same bytes, and the guard now recognises that case.

## Test evidence

- `python3 -m pytest tests/test_lumen_pr_dirty_guard.py -q` in the lumen-bin worktree: 25 passed
  (17 pre-existing + 8 new wq#1490 cases: identical uncommitted → clean; divergent uncommitted → still
  refused; no `--head` → still refused; untracked identical → clean; symlink → refused; ffprimary over
  identical overlap → fast-forwards with no byte change; ffprimary over divergent overlap → refused;
  superset re-point over identical overlap → re-points).
