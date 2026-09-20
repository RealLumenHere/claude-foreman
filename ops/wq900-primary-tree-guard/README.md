# primary-tree-guard (wq#900)

Audit 2026-08-23 found /home/lumen/foreman's PRIMARY checkout on a worker's
feature branch — the live dispatcher/self-heal/health timers were executing
unreviewed code. Same class previously hit /home/lumen/bin (wq#867).

Three walls, one per layer:

1. **Deny at the tool call** — `harness/guard.sh` (wq#900 section) blocks any
   HEAD/branch-moving git verb (`checkout|switch|restore|reset|rebase|merge|pull`)
   when the hook cwd is inside a registered primary tree or the command targets
   one via `cd` / `git -C` / `--git-dir` / `--work-tree`. Registry comes from
   `FOREMAN_PRIMARY_TREES` (set by fworker from `router.LANE_REPO` +
   `/home/lumen/bin`), with a static fallback in guard.sh. One sanctioned
   exception: `git merge --ff-only origin/main|master` — the deploy fast-forward
   every landed PR needs; it can't switch branches or add unreviewed content.
2. **Post-run backstop** — `fworker.run_child` calls
   `primary_tree_guard.check_and_restore_all()` after every child exits: restores
   a clean drifted primary and ntfy-alerts, seconds after an evasion.
3. **This timer** — every 5 minutes, `primary_tree_guard.py check` asserts every
   primary is on its default branch; auto-restores when the tree has no modified
   tracked files, alerts (dedup'd per drift episode) either way. Catches drift
   from anything that isn't an fworker child (manual sessions, other hosts).

4. **Stale-dirty page (wq#1540)** — the same timer pages when any primary (now
   including `/home/lumen/minerva`) has had modified tracked files for >24h,
   on any branch, and re-pages daily while it stays that way. First-seen time
   lives in `/tmp/.primary-tree-dirty.state` (`PRIMARY_TREE_DIRTY_STATE`); a tree
   seen dirty with no record is dated from its oldest modified file's mtime. Clear
   it by landing the edits via lumen-pr, or untracking + `.gitignore`-ing data files.

Install (root, via `lumen-run --root`): `./install.sh`
Manual check: `python3 /home/lumen/foreman/harness/primary_tree_guard.py status`
