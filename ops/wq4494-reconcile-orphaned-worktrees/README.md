# wq#4494 — reconcile orphaned unregistered checkouts

`/home/lumen/worktrees/worktrees` held 2059 `wqNNNN-aN` dirs plus
`repo-verify-main`, dated 2026-08-22..2026-10-02, 46G — a nested copy of the
`worktrees` dir, created some time around 2026-10-05 (parent mtime), that
nothing owns. `lumen-prune-worktrees.timer`
(`ops/wq2620-prune-stale-worktrees`) is healthy but only scans **direct**
children of its roots, so it never descends into the nested copy, and every
dir inside it fails `prune_worktrees.py`'s own safety check anyway: each
one's `.git` file still points at an admin dir
(`REPO/.git/worktrees/NAME`) that no longer exists, so git cannot report
dirty/unpushed state and the pruner's checks fail closed (dry run:
`scanned=2059 prunable=0`, every skip reason `not registered in git worktree
list`).

**Root cause check (build step 1):** `systemctl list-timers --all` on Lumen
shows no recurring job that writes under `/home/lumen/worktrees` other than
`lumen-prune-worktrees.timer` itself, which only deletes, never copies. There
is no second mirror/backup timer to stop — the duplication was a one-time
event around 2026-10-05, not an ongoing process. (User crontab could not be
checked from this worktree — `crontab -l` is not on the Bash allowlist here;
flagging rather than guessing.)

`reconcile_orphaned_worktrees.py` is the tool for the debris itself. For
every entry directly under `--root` (default
`/home/lumen/worktrees/worktrees`):

1. **Confirm it's actually orphaned** (`is_orphaned`) — its `.git` file
   resolves to a gitdir that genuinely doesn't exist on disk. Anything still
   registered is left alone; that's `prune_worktrees.py`'s job, not this
   one.
2. **Map the name to a wq ticket** (`wqNNNN-aN`) and require
   `tasks.status == 'done'` in the live queue db. No ticket id in the name,
   no row, or any non-done status all skip — fail closed, same convention as
   `prune_worktrees.py`'s `wq_claim_is_live`.
3. **Archive before delete** — tar+zstd the dir, test-extract the archive
   into a scratch dir, byte-compare every file against the source, and only
   then delete the source. A failed archive or a failed verify skips instead
   of deleting.
4. **Log every decision** — one JSON line per run to `--log`, dry-run or
   applied.

```
python3 reconcile_orphaned_worktrees.py                     # dry run, human summary
python3 reconcile_orphaned_worktrees.py --json              # dry run, full JSON
python3 reconcile_orphaned_worktrees.py --apply             # archive + delete for real
python3 reconcile_orphaned_worktrees.py --root R            # override the scanned dir
python3 reconcile_orphaned_worktrees.py --archive-root R    # override the archive destination
```

Default archive destination is local
(`/home/lumen/backups/wq4494-orphaned-worktrees`) rather than a guessed path
on Thoth: the fleet's offsite copy (`lumen-backup-offsite.service`, restic to
Thoth) already picks up anything under `/home/lumen/backups` on its own
schedule, and no verified Thoth-side path for this specific archive exists
yet to hardcode — invent one and you've guessed a path, which the standing
doctrine here rules out. Pass `--archive-root` to point at a different
destination once one exists.

Not every entry resolves this way: `repo-verify-main` has no `wqNNNN-aN`
ticket id in its name, so it is always skipped for manual review, never
auto-archived.
