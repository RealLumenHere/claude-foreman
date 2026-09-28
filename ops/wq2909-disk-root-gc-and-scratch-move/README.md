# wq#2909 — root disk: worktree GC grace period, and the archive/restic move decision

fleet-audit-2 (2026-09-28) found root at 253G/466G, with `/home/lumen/worktrees`
(1547 dirs, 34G), `/data/lumen/archive/re` (147G), and the restic repo
`/data/lumen/backups` (97G) as the three big non-OS consumers. This ticket's four
FIX items and what actually happened to each, working within this session's
sandbox (this worktree + `--add-dir` roots only — `/data/lumen` and
`/home/lumen/services` are outside it, see "Blocked" below):

## 1. Worktree janitor — already shipped, one real gap fixed

wq#2620 already built and deployed exactly this: `lumen-prune-worktrees.timer`
is installed, `enabled`, and has run daily since 2026-09-26 (`systemctl
list-timers` confirms a run 7h before this session started). Its pruner
(`ops/wq2620-prune-stale-worktrees/prune_worktrees.py`) already requires
merged + clean + registered/unlocked + no live wq claim or slock before
removing a worktree — so most of the 1547 directories still on disk are
*correctly* skipped (dirty or genuinely unmerged work, confirmed by reading
`/home/lumen/kb/logs/wq2620-prune-worktrees.jsonl`), not a bug.

The one real gap against this ticket's own spec — "branch is merged **or PR
closed for >7d**" — is that the pruner had no time dimension at all: it pruned
the instant `is_merged` went true, with no window for a same-day revert to
still find its worktree. Fixed in
`ops/wq2620-prune-stale-worktrees/prune_worktrees.py:190-206` (new
`merge_age_days`) and `prune_worktrees.py:287-308` (`classify` now takes
`grace_days` and skips anything younger than it, default 7, fails closed on an
unreadable commit date). Flag added at `prune_worktrees.py:406-408`
(`--grace-days`, default 7). Tests:
`ops/wq2620-prune-stale-worktrees/tests/test_prune_worktrees.py:278-306` (new
grace-period + fail-closed cases); full suite still green, 20/20
(`python3 -m pytest tests -q` in that directory).

Production dry-run comparison, this session, `--no-fetch` both times:

- Old behavior (`--grace-days 0`): scanned=1643 prunable=43 size=667M
- New default (`--grace-days 7`): scanned=1643 prunable=0 size=0B

Zero prunable today isn't a regression — the daily timer already swept
anything merged more than a day ago before this session started, so at
`--grace-days 7` nothing in today's snapshot is old enough yet. Tomorrow's
scheduled run (next fire per `systemctl list-timers`) will start reclaiming
the 43-worktree/667M backlog as each one clears its 7-day window, at the same
daily cadence the timer already runs at. No further action needed for item 1;
the timer picks this change up automatically since it invokes
`prune_worktrees.py` with no `--grace-days` override (see
`lumen-prune-worktrees.service`), so it now gets the new default for free.

## 2. Restic `--cleanup-cache` — BLOCKED, outside this session's filesystem bounds

The backup scripts that invoke restic live under `/home/lumen/services`
(e.g. `services/backup/scripts/backup-critical.sh`, per ISSUE-LOG #375), which
is not in this session's `--add-dir` list and refuses with "may only list
files in the allowed working directories" on any read attempt — a path bound,
not a command-shape problem, so no rewrite of the command reaches it (see
CLAUDE.md's Bash-allowlist rules). Needs a dispatch with
`--add-dir /home/lumen/services` (read+write) to land the `--cleanup-cache`
flag. Filed as a follow-up rather than guessed at.

## 3. Move `/data/lumen/archive` (147G) and `/data/lumen/backups` (97G) to /scratch

**Decision (recorded, not gated — full-autonomy standing order): do the move.**
`/scratch` (md1, RAID, 1.9T, 1.7T free per the audit) has ample headroom for
both (244G combined) with room to spare; root would drop from 253G to roughly
109G. Keep `/data/lumen/archive` and `/data/lumen/backups` as the canonical
paths via symlinks into `/scratch/archive` and `/scratch/backups` respectively
so nothing that reads those paths today needs to change.

Procedure (restic-native, per the ticket's own instruction — never a shortcut
`mv` on a live repo):

1. `restic -r /data/lumen/backups copy -r /scratch/backups` (restic's own
   cross-repo copy, not a filesystem copy — keeps the repo's own integrity
   metadata rather than trusting a raw copy of on-disk blobs).
2. `restic -r /scratch/backups check --read-data` on the new copy before
   touching anything live.
3. Point `RESTIC_REPOSITORY` (or whatever env/config file sets it — needs
   locating under `/home/lumen/services`, itself blocked per item 2) at
   `/scratch/backups` and run one full backup cycle against it.
4. Only after that cycle succeeds: remove `/data/lumen/backups`, symlink it to
   `/scratch/backups`.
5. `/data/lumen/archive/re` (147G, a project sync with no write-through
   concerns like a live restic repo) is a plain `rsync -a` to
   `/scratch/archive/re`, byte-for-byte verify (`diff -rq` or checksums), then
   swap `/data/lumen/archive` for a symlink. `/home/lumen/re` already points at
   `/data/lumen/archive/re` and needs no change once the symlink target moves.

**BLOCKED on execution, not on the decision**: `/data/lumen`,
`/scratch/backups`, `/scratch/archive`, and the restic repo itself are all
outside this session's `--add-dir` list, so none of the actual `restic copy`/
`rsync`/symlink-swap steps above can run from this sandbox — this is the exact
same class of path-boundary refusal as item 2, not a decision left for Frank.
Filed as a follow-up dispatch needing `--add-dir /data/lumen --add-dir
/scratch` (read+write on both).

## 4. Root-fs usage line + 75% alarm in fleet-audit — BLOCKED, script not in reach

No file matching `fleet-audit`/`fleet_audit` exists anywhere under this
session's worktree or its other `--add-dir` roots (`agents/work-queue`,
`mailbot`, `pokemon`, `bin`, `foreman`, `kb`, `projects/nest`,
`/tmp/fworker-wq2909` — searched all of them). It lives somewhere else in the
fleet (plausibly under `/home/lumen/services` alongside the backup scripts,
consistent with item 2's finding). Same fix as items 2/3: needs a dispatch
with that path added before a `df /`-plus-alarm line can be added to it.
