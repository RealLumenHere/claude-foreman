## What

Installs wq#3171's already-shipped 90% capacity alert for `/data/lumen`
(it existed in the repo but its `install.sh` had never been run), and adds
`ops/wq3206-data-lumen-growth/`: a daily `df` snapshot logger + 30-day
GB/day slope reporter for the same mount.

## Why

/data/lumen (md0) was at 97% with no live capacity alert and no growth
history to judge urgency (overnight-1 health sweep, 2026-09-30). The
raise/clear alarm (wq#3171) answers "is it over the line now"; this adds
"how fast is it filling", so the next capacity ticket has trend data
instead of a single point-in-time `du`.

## How

- Ran `lumen-run start --root -- bash ops/wq3171-disk-data-lumen-alert/install.sh`
  to install and enable `lumen-disk-pct-alarm.timer` (previously shipped,
  never deployed).
- Added `data_lumen_growth.py` (`record` appends a TSV row via
  `shutil.disk_usage`; `slope` reports trailing GB/day) plus a matching
  systemd `.service`/`.timer`/`install.sh` following the wq#3171/wq#2620
  ops-bundle convention (`--verify` contract from `ops/_verify.sh`).
- Logged findings to `kb/ISSUE-LOG.md` row 1102: the alert gap, and that
  `ops/wq2620-prune-stale-worktrees/prune_worktrees.py`'s dry-run against
  `/data/lumen/lumen-home/worktrees` found all 1442 dirs unregistered in
  `git worktree list` (0 prunable) — trim candidates there remain blocked,
  this worker has no `--add-dir /data/lumen` grant.

## Testing

- `python3 -m pytest ops/wq3206-data-lumen-growth/tests -q` — 9/9 passing
- `lumen-run start --root -- bash .../wq3171.../install.sh --verify`: `MISS x4` before install -> `VERIFY OK` after
- `python3 disk_pct_alarm.py check --dry-run --json`: `/data/lumen` 91.9%, `firing: true`
- `ops/wq3206-data-lumen-growth/install.sh --verify` not yet run against `/etc`
  — its unit's `ExecStart` points at the stable `/home/lumen/foreman/ops/...`
  path, so install needs this branch landed first (follow-up step, noted in
  `kb/ISSUE-LOG.md` row 1102).

## Risk/rollback

Growth logger is read-only (`shutil.disk_usage` + TSV append), no writes to
`/data/lumen` itself. Both timers roll back the same way:
`systemctl disable --now <timer>; rm /etc/systemd/system/<service> /etc/systemd/system/<timer>`
(printed by each `install.sh`). No application data touched.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
