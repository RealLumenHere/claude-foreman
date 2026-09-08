# wq#1523 — [repo-sweep] foreman: BEHIND_ONLY fix

## Ticket

Automated sweep flagged `/home/lumen/foreman` as `BEHIND_ONLY main ahead=0 behind=5`.
Not a code-PR ticket — an operational fix on the live primary tree, per
`harness/repo_reconcile.py`'s own scope note ("Not this tool's job").

## Diagnosis

`python3 /home/lumen/foreman/harness/repo_reconcile.py status --repo /home/lumen/foreman`
confirmed: `BEHIND_ONLY /home/lumen/foreman main ahead=0 behind=5` (exit 30).
`ahead=0` means the fast-forward itself carries no risk of losing local commits, but
`git -C /home/lumen/foreman status --porcelain=v1` showed 5 untracked files that a naive
fast-forward can collide with (the wq#1279 failure mode):

- `ops/wq1184-gatus-health-checks/deadman_state.json`
- `plans/wq1270.md`
- `plans/wq1421.md`
- `plans/wq1435.md`
- `reports/fleetops_backlog_broker_2026-09-08.md`

`git -C /home/lumen/foreman log --name-only main..origin/main` was checked against those
5 paths before merging: no overlap, so the untracked files were not blocking the merge,
only at risk of being silently clobbered by a working-tree checkout race.

## Fix

1. `git -C /home/lumen/foreman stash push -u -m "wq1523-preff-untracked"` — held the 5
   untracked files aside. Captured SHA via
   `git -C /home/lumen/foreman stash list --format='%H %gs'`:
   `779d374622494ae47267d20d8fb41c1dd9f537c2`.
2. `git -C /home/lumen/foreman fetch origin` then
   `git -C /home/lumen/foreman merge --ff-only origin/main` — clean fast-forward
   `67571f5..5fb57fe`, 18 files changed (liveness backlog sweep wq#1492, fleet-exec
   policy wq#1297, preflight scrub fix). No conflicts.
3. `git -C /home/lumen/foreman stash apply 779d374622494ae47267d20d8fb41c1dd9f537c2` —
   restored all 5 untracked files byte-for-byte (confirmed via `git status --porcelain`
   listing the same 5 paths, none tracked/modified).
4. `git -C /home/lumen/foreman stash drop stash@{0}` — dropped only the captured entry
   (verified by SHA match against `stash list` before dropping); the 3 pre-existing,
   unrelated stash entries on the shared stack (`main` WIP, `chore/wq856-...`,
   `feat/wq749-...`) were left untouched.

## Verification

`python3 /home/lumen/foreman/harness/repo_reconcile.py status --repo /home/lumen/foreman`
now reports:

```
IN_SYNC /home/lumen/foreman main ahead=0 behind=0
main == origin/main at 5fb57fe. Nothing to do.
```

exit 0. `git -C /home/lumen/foreman status --porcelain=v1` shows only the same 5
untracked files, nothing else — no tracked-file drift, no lost work.

**Verdict: SOLVED.** Logged as ISSUE-LOG row for future repo-sweep BEHIND_ONLY tickets
on `/home/lumen/foreman` (same pattern as wq#1279/wq#1280).
