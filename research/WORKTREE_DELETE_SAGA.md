# WORKTREE_DELETE_SAGA -- archive-first worktree deletion for foreman (wq#4866)

Status: DESIGN ONLY. No code, no deletions, no timer changes in this ticket.
Author: eval-foreman-patterns-b39338 (claude.ai orchestrator, 2026-11-05).
Parent research: scratch/research/paradev_ccc_patterns.md section 3 (Paradev
host-service/trpc/router/workspace-cleanup.ts:195-490, MIT).
Closes the design gap behind wq#4494 (2,059 orphaned checkouts) and hardens
ops/wq2620-prune-stale-worktrees/prune_worktrees.py (lumen-prune-worktrees.timer).

## 0. Verified state on Lumen, 2026-11-05 02:30 PST (read-only probes)

| fact | value | how verified |
|---|---|---|
| fworker worktree root | /home/lumen/worktrees (FOREMAN_WORKTREES_ROOT default) | harness/fworker.py:119 |
| per-ticket shape | /home/lumen/worktrees/wqNNNN-aN, branch `<type>/wqNNNN-<slug>-aN` | fworker.py make_worktree() |
| orphan dir | /home/lumen/worktrees/worktrees -> symlink -> /scratch/fleet/worktrees (md1, 1.9T, 42% used) | readlink -f, findmnt |
| orphan count | 2,062 entries (2,059 wqNNNN-aN + repo-verify-main + .pytest_cache + 1 other) | ls \| wc -l |
| orphan shape | each has a `.git` FILE: `gitdir: /home/lumen/foreman/.git/worktrees/<name>` | cat wq1014-a3/.git |
| admin dir | /home/lumen/foreman/.git/worktrees/wq1014-a3 does NOT exist | ls |
| registry | `git worktree list --porcelain` in foreman = 263 entries, ZERO under worktrees/worktrees | grep -c |
| git on an orphan | `fatal: not a git repository` -- git cannot operate in them at all | git -C ... status |
| size | ticket says 46G; a 60 s `du -s` returned 0 blocks (suspicious: likely killed by timeout or sparse reporting). RE-MEASURE before sizing archives (phase 0) | timeout 60 du -s |
| pruner scope | DEFAULT_ROOTS = scratch/worktrees + /home/lumen/worktrees; walks ONE level, criterion (c) requires registry membership | prune_worktrees.py |
| pruner cadence | lumen-prune-worktrees.timer daily (last 2026-11-05 00:26 UTC), `--apply` | systemctl list-timers / cat |
| git lock | fworker takes `_gitlock_scope(repo)` (same slock as `lumen-pr land`) around worktree prune/add/remove (wq#1141) | fworker.py:2434-2450 |
| mount gotcha | limpet/iso-builder/build.sh:134 + test_wq4710_teardown.py: a stale mount under a worktree makes a recursive delete descend into host device nodes | grep |

Root-cause hypothesis for the orphans (NOT verified, phase 0 verifies): the nested
`worktrees/worktrees` name plus the dangling gitdir pointers match a path swap of
/home/lumen/worktrees (claude_memory misc:disk-layout-rules: wq#2094 dir<->symlink
swap, wq#3532 decision to keep real dirs). After the swap, fworker's
prune-on-start (`git worktree prune`, fworker.py:2452) saw every admin entry
pointing at a path that "no longer existed" and dropped them, leaving the
directories behind with no registry entry. That is exactly the class Paradev's
saga prevents: delete intent must be durable BEFORE git bookkeeping changes.

Consequence for this design: there are TWO populations, and only one is a git
worktree any more.
- Population A (live root, 263 registered): `git worktree remove` works. The
  existing pruner handles the safe subset; the saga wraps it.
- Population B (2,059 unregistered dirs): `git worktree remove` is impossible
  (git refuses to see them). They are plain directories that happen to contain
  a foreman checkout. Removal is a filesystem delete; archive must be computed
  by BORROWING the main repo's object store (section 4.3), never by trusting the
  dir's own metadata.

## 1. Problem

`make_worktree()` has a creation story (adopt-if-ours, gitlock, prune-on-start)
and no deletion story. Deletion today is (a) the daily pruner, which by design
never touches an unregistered dir, and (b) `git worktree remove --force` /
`shutil.rmtree` inside make_worktree when a stale dir is in the way -- an
UNARCHIVED delete of someone else's prior attempt. Nothing writes an intent
record, nothing archives, nothing reconciles after a crash, and nothing stops
two actors (pruner, fworker adopt path, an operator) from racing on one path.

Goals, in priority order:
1. Never lose unique work (uncommitted changes, unpushed commits) without an
   archive that was verified BEFORE the delete became reachable.
2. Never delete a worktree that is in use (live claim, live lumen-run unit,
   active slock, mountpoint underneath).
3. Finish what was started: a crash mid-delete leaves a tombstone, and the next
   run completes it from the recorded step (idempotent steps).
4. Drain Population B at a bounded cost per run, with a paper trail per dir.
5. One removal path for everybody (pruner, fworker, operator), so there is one
   place to audit.

Non-goals: moving the roots to /scratch (wq#3532 decided no), changing branch
naming, touching lumen-pr land.

## 2. Saga overview (adapted from Paradev workspace-cleanup.ts)

```
request(path, reason, actor) 
  -> S0 TOMBSTONE   durable intent row, status=pending              [idempotent]
  -> S1 PREFLIGHT   classify (A/B), in-flight guard, dirty check,
                    mountpoint check; UNKNOWN = fail closed          [read-only]
  -> S2 ARCHIVE     bundle + dirty-file tarball to archive dir,
                    verify, write manifest; row.archive_path set    [idempotent]
  -> S3 TEARDOWN    nothing to stop (we never delete a live tree);
                    re-check mounts right before delete             [read-only]
  -> S4 REMOVE      A: git worktree remove --force --force + prune
                    B: recursive delete of a plain dir
                    under the repo gitlock                           [idempotent]
  -> S5 BRANCH      `git branch -D` ONLY with explicit consent flag
                    AND tip reachable from origin/<default>          [optional]
  -> S6 SETTLE      status=done|failed(<typed reason>), settled_at
```
Steps S1-S4 each re-read the tombstone row and skip work already recorded as
done, so a crash at any point is resumed by the reconciler (section 6) rather
than retried from scratch or abandoned.

## 3. Data model

New SQLite file `/home/lumen/agents/work-queue/worktree_saga.db` (NOT a new
table in queue.db: bin/wq owns that schema and its migrations; the pruner only
reads queue.db today and should stay read-only on it). WAL mode, busy_timeout
5000 ms, single writer by construction (section 7).

```sql
CREATE TABLE tombstones (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  path          TEXT NOT NULL,                 -- canonical realpath of the dir
  repo          TEXT,                          -- main repo root, NULL for pop. B until resolved
  population    TEXT CHECK(population IN ("registered","unregistered","unknown")) DEFAULT "unknown",
  ticket_id     INTEGER,                       -- from wqNNNN-aN name, NULL otherwise
  attempt       INTEGER,
  branch        TEXT,                          -- ref name if resolvable
  reason        TEXT NOT NULL,                 -- merged_and_aged | orphan_backfill | adopt_path_clear | operator
  requested_by  TEXT NOT NULL,                 -- WQ_AGENT / unit name / "prune_worktrees.py"
  requested_at  TEXT NOT NULL,                 -- %Y-%m-%d %H:%M:%S UTC (same format as queue.db, wq#2669)
  status        TEXT NOT NULL DEFAULT "pending"
                CHECK(status IN ("pending","preflight_ok","archived","removed","done",
                                 "failed","deferred","superseded")),
  step          INTEGER NOT NULL DEFAULT 0,    -- last COMPLETED saga step (0..6)
  fail_code     TEXT,                          -- typed: see section 5
  fail_detail   TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  next_try_at   TEXT,                          -- backoff for deferred rows
  archive_path  TEXT,                          -- dir written by S2
  archive_sha   TEXT,                          -- sha256 of manifest.json
  bytes_before  INTEGER,                       -- du -s of path at S1 (bytes)
  delete_branch INTEGER NOT NULL DEFAULT 0,    -- consent flag for S5
  settled_at    TEXT,
  run_id        TEXT                           -- saga run that last touched the row
);
CREATE UNIQUE INDEX ux_tombstone_live ON tombstones(path)
  WHERE status NOT IN ("done","failed","superseded");   -- one live intent per path
CREATE INDEX ix_tombstone_pending ON tombstones(status, next_try_at);
```
The partial unique index is the in-flight guard (Paradev step 5): a second
request for a path with a live tombstone gets a typed CONFLICT(DELETE_IN_PROGRESS)
from the INSERT, never a force-retry.

Archive layout (section 4.3), one dir per tombstone:
```
/scratch/fleet/worktree-archive/<YYYY-MM>/<basename>-<tombstone_id>/
  manifest.json       # path, repo, population, branch, head_sha, ticket, reason,
                      # file lists, sha256 of every artifact, tool versions, timings
  branch.bundle       # git bundle of commits NOT reachable from origin/<default> (may be absent)
  dirty.tar.zst       # modified + untracked files (may be absent)
  git-status.txt      # raw porcelain output used for the decision
```
Retention: 30 days, then a SEPARATE pruner run (`--prune-archives`) deletes
archive dirs whose tombstone is `done` and older than 30 d. Archives are never
deleted in the same run that created them.

## 4. Steps in detail

### S0 Tombstone (durable intent)
INSERT the row with status=pending, step=0. If the partial unique index
rejects it: return CONFLICT, do nothing else. This is the only step an actor
other than the saga runner may execute (section 7): fworker and the pruner
REQUEST deletions; the saga runner performs them.

### S1 Preflight -- read-only, every check fails CLOSED
Order matters; the first failure stops the step and records fail_code.
1. `realpath` must be directly under one of the allowed roots
   (/home/lumen/worktrees, /home/lumen/scratch/worktrees,
   /scratch/fleet/worktrees). Anything else -> failed(PATH_OUT_OF_SCOPE).
   Never follow a symlink target outside the roots.
2. Classify: `.git` must be a FILE with a `gitdir:` line (a `.git` DIRECTORY
   is a primary checkout -> failed(PRIMARY_CHECKOUT), same rule as the pruner).
   Admin dir exists AND path appears in `git worktree list --porcelain` ->
   population=registered; else population=unregistered. Repo = the prefix
   before `/.git/worktrees/`; it must itself be a git repo or -> failed(REPO_GONE).
3. In-flight guard (the four liveness sources the fleet already has):
   a. wq claim: name wqNNNN-aN -> `tasks.status='claimed'` with unexpired
      `lease_expires` (parse with `%Y-%m-%d %H:%M:%S`, wq#2644) -> deferred(LIVE_CLAIM).
   b. lumen-run unit: `systemctl --user list-units 'lumen-run-*'` (or the
      system scope lumen-run uses -- verify in phase 0) whose unit file or
      ExecStart cites the path -> deferred(LIVE_UNIT).
   c. slock: holder row in queue.db `slocks` / `slocks_ro` whose name or
      note cites the path, unexpired -> deferred(LIVE_SLOCK).
   d. gitlock: `_gitlock_scope(repo)` currently held -> deferred(GITLOCK_BUSY),
      retry in minutes (same posture as make_worktree).
4. Mountpoint check: `findmnt -R -n -o TARGET --target <path>` plus a scan for
   any mount whose TARGET starts with `<path>/` -> failed(MOUNT_UNDER_TREE).
   This is the wq#4710 rule generalised: never recurse into a tree with a mount
   beneath it. Also refuse if any entry under the path is a block/char device
   node (`find -type b,c` bounded to 60 s).
5. Dirty check (population A): `git -C <path> status --porcelain --untracked-files=all`
   with `timeout 90`. Non-zero or timeout -> status UNKNOWN -> deferred(DIRTY_UNKNOWN)
   with backoff; never treated as clean. (Paradev: "timeout = UNKNOWN = fail closed".)
   Dirty is NOT a block for the saga (unlike the pruner): dirty means "archive
   it", because the archive is the point. It IS a block for the
   `merged_and_aged` reason, to keep the existing pruner contract.
   Dirty check (population B): section 4.3, borrowed-object-store diff.
6. Record `bytes_before` (`du -sb`, 120 s timeout; timeout -> NULL, not a
   failure). Set status=preflight_ok, step=1.

`--force` (operator only, logged) skips ONLY the dirty-check outcome, never the
in-flight guard or the mount check. There is no flag that skips archiving.

### S2 Archive -- the step that makes deletion safe
Archive is computed, written, VERIFIED, and recorded on the row before S4 is
reachable. A row with step < 2 can never be removed. Contents are minimal (we
do not copy 46 G): only what is unique to this tree.

Population A (registered):
- `head=$(git -C path rev-parse HEAD)`; `default=origin/<default>` as the
  pruner resolves it.
- Unique commits: `git -C repo bundle create <archive>/branch.bundle
  <default>..<branch>` when `git rev-list --count <default>..<branch>` > 0.
  Then `git bundle verify branch.bundle` must pass.
- Dirty/untracked: list = `git status --porcelain -z --untracked-files=all`;
  `tar --zstd -cf dirty.tar.zst -C <path> --null -T <list>`; then
  `tar -tf dirty.tar.zst` must succeed and entry count must equal list count.
- If both lists are empty, write manifest.json with `"unique": false` and no
  artifacts. That IS a valid archive (nothing to keep, recorded as such).

Population B (unregistered, git refuses to run inside):
Borrow the main repo's object store with a throwaway index so the main
worktree's index is never written:
```
tmpidx=$(mktemp)
export GIT_INDEX_FILE=$tmpidx
git --git-dir=<repo>/.git --work-tree=<path> read-tree HEAD
# files whose content differs from main HEAD (includes the orphan's own commits
# if its branch was never landed -- that is the point)
git --git-dir=<repo>/.git --work-tree=<path> diff --name-only -z HEAD   > changed.z
git --git-dir=<repo>/.git --work-tree=<path> ls-files -z --others --exclude-standard > untracked.z
```
Both run under the repo gitlock (they read HEAD and refs) with a 180 s timeout;
timeout -> deferred(DIRTY_UNKNOWN). Branch recovery: the branch name is
deterministic from (ticket, attempt) -- `git for-each-ref 'refs/heads/*/wq<N>-*-a<M>'`
-- so if the ref still exists, bundle `<default>..<branch>` exactly as for A.
Then tar the changed+untracked list as above. The comparison base is main
HEAD, not the branch tip, so the tarball may include files the branch already
committed; accepted (over-archiving is cheap, under-archiving is the bug).

manifest.json is written last, sha256 of it stored in archive_sha, row ->
status=archived, step=2. Any verify failure -> deferred(ARCHIVE_VERIFY_FAILED)
with the partial archive dir renamed `*.partial` (never reused).

### S3 Teardown
Paradev runs a user teardown script here. Foreman has no per-worktree
services, and S1 already refused live units, so S3 is: re-run S1 checks 3 and 4
(cheap) immediately before S4, because minutes may have passed during S2.
Any change -> deferred, keep the archive.

### S4 Remove -- the ONLY removal path
Under `_gitlock_scope(repo)` (fworker.py helper, same slock as lumen-pr land):
- Population A: `git -C repo worktree remove --force --force <path>` (second
  --force: remove even if locked -- we already proved nobody holds it), then
  `git -C repo worktree prune`. "is not a working tree" / "does not exist" after
  the call is treated as success (Paradev 3c: dir already gone).
- Population B: `git -C repo worktree prune` (no-op, keeps registry honest),
  then `shutil.rmtree(path, onerror=<log and re-raise>)` with the S3 mount
  re-check done INSIDE the same lock window.
- A `.git` DIRECTORY appearing at the path at this point (someone turned it into
  a primary checkout since S1) -> failed(PRIMARY_CHECKOUT), nothing deleted.
Row -> status=removed, step=4.

### S5 Branch
Only if `delete_branch=1` (explicit consent, never the default for
orphan_backfill) AND the tip is reachable from <default>:
`git -C repo branch -D <branch>`. Unreachable tips are kept even with consent:
the bundle holds the commits, but a ref costs nothing and is easier to find.

### S6 Settle
status=done, settled_at; append one JSONL line to
/home/lumen/kb/logs/worktree-saga.jsonl (same paper-trail posture as
wq2620-prune-worktrees.jsonl) with the full row + archive manifest sha.

## 5. Typed outcomes (fail_code)

| code | status | retry |
|---|---|---|
| DELETE_IN_PROGRESS | (INSERT rejected) | caller backs off, never forces |
| PATH_OUT_OF_SCOPE, PRIMARY_CHECKOUT, REPO_GONE, MOUNT_UNDER_TREE | failed | no -- needs a human |
| LIVE_CLAIM, LIVE_UNIT, LIVE_SLOCK, GITLOCK_BUSY | deferred | yes, next_try_at = now + 30 min (x2 per attempt, cap 24 h) |
| DIRTY_UNKNOWN, ARCHIVE_VERIFY_FAILED | deferred | yes, same backoff; after 5 attempts -> failed |
| DIR_GONE | done | n/a -- reconciler closes it |
These map onto wq's block_reason taxonomy so a saga failure can be surfaced as
a ticket note or an aq row without inventing a second vocabulary.

## 6. Startup reconciler (Paradev step 0, "finish a crashed delete")

Every saga run begins with:
1. `SELECT * FROM tombstones WHERE status NOT IN (done,failed,superseded)`.
2. For each: if the path no longer exists -> status=done, fail_code=DIR_GONE
   (and `git worktree prune` for its repo). Else resume at `step+1`.
3. Rows older than 7 days still not done -> aq row `worktree-saga:stuck:<id>`
   (fleet already triages aq), not a silent loop.
This is also what makes the pruner safe to crash: the pruner no longer removes
anything itself (section 7), it only files tombstones.

## 7. Who may do what

| actor | may | may not |
|---|---|---|
| prune_worktrees.py (daily timer) | compute its 5 criteria, then INSERT tombstone(reason=merged_and_aged) | call `git worktree remove` directly (today's line 581 path is retired) |
| fworker make_worktree() | INSERT tombstone(reason=adopt_path_clear) when a stale dir blocks it, then DEFER the attempt (exit = redispatch) | `worktree remove --force` / `shutil.rmtree` inline (today fworker.py:2464-2466); adopt a path that has a live tombstone |
| saga runner (`worktree_saga.py run`) | S1-S6 under slock `worktree-saga` (single-flight), per-run budget: max 50 rows, 20 min wall | run two instances; skip S2 |
| operator | `worktree_saga.py request <path> --reason operator [--force] [--delete-branch]`; `status`; `retry <id>`; `restore <id>` (untar + bundle unbundle into a NEW worktree) | hand-delete anything under the roots (HANDOFF section 7 rule stands) |

The saga runner is invoked by the SAME lumen-prune-worktrees.service
(`prune_worktrees.py --apply` becomes: file tombstones, then run the saga)
so no new timer is added (10-08 timer massacre lesson). A one-shot
`--backfill-orphans` mode files Population B tombstones at a bounded rate
(default 200/run) with reason=orphan_backfill, delete_branch=0; at 50 sagas
per daily run the 2,059 drain in about six weeks, archive-first, with a JSONL
line each. Faster drain = raise the budget deliberately, not by hand-deleting.

## 8. Changes this implies (each its own ticket; none done here)

1. Phase 0 -- measure and verify: re-run `du -sb /scratch/fleet/worktrees`
   detached with progress; confirm the root-cause hypothesis via the oldest
   orphan's mtime vs wq#2094/#3532 dates; confirm lumen-run unit scope for
   guard 3b; sample 20 orphans through the Population B diff to size archives.
2. Phase 1 -- `worktree_saga.py` library + CLI + db, dry-run only, tests
   against a scratch repo (same fixture pattern as fworker's CAL-1).
3. Phase 2 -- pruner integration: prune_worktrees.py files tombstones instead
   of removing; saga runs in the same service invocation.
4. Phase 3 -- fworker integration: make_worktree() files
   adopt_path_clear tombstones and defers; refuses to adopt tombstoned paths.
5. Phase 4 -- orphan backfill enabled with Frank's explicit go
   (`--backfill-orphans` is a no-op until a config flag is set).
6. Phase 5 -- archive retention + `restore` command + aq stuck rows.

## 9. What is deliberately NOT adopted from Paradev
- Worker-pool recursive delete off the event loop: foreman's runner is a
  one-shot process; a 20-minute budget replaces it.
- Teardown scripts: no per-worktree services exist.
- Archive-in-UI / un-archive: the fleet's "archive" is the tarball + bundle;
  `restore` recreates a worktree from them instead.

## 10. Open questions for Frank
- Archive root `/scratch/fleet/worktree-archive` (same md1 array as the
  orphans) -- acceptable, or Thoth (re-archive store) for anything older than 7 d?
- `delete_branch` default for `merged_and_aged` (pruner reason): propose 1
  (the pruner already proved the tip landed); for everything else 0.
- Drain rate 50/day vs a one-time Frank-watched bulk run.
