# wq#3171 — /data/lumen (md0 RAID5, 3/3 healthy) at 97%: 428G/469G used, 18G free

fleet-audit-2's `du` (scratch/audit_20260929/du_data_lumen.txt, parent wq#3162)
broke down the 428G as archive/re 147G, gocheckin-20260811 23G,
samsung-migration-20260527 17G, sandbox 6.8G (archive 199G total); backups/data
118G (backups 120G total); miko_backup 42G, bets 16G (projects 65G); worktrees
31G, cache 16G (lumen-home 46G). Root is 65%/157G free, `/scratch` 15% (1.9T,
RAID0, non-redundant), `/mnt/lumen-backup` 38% (1.2T). Three FIX items, what
shipped here and what's blocked on sandbox scope:

## 1. Prune `/data/lumen/lumen-home/worktrees` (31G) — BLOCKED, out of reach

`/data/lumen` is outside every `--add-dir` root this worker was granted
(`ls`/`du` on it refuse with "may only list files in the allowed working
directories"), identical to wq#2909's finding for the same path. This is the
same class of block as wq#2909 items 2-4, not a Frank-gated decision — the
worktree-janitor pattern to apply here already exists and works
(`ops/wq2620-prune-stale-worktrees/prune_worktrees.py`, extended by wq#2909
with a merge-age grace period); it just needs pointing at
`/data/lumen/lumen-home/worktrees` instead of (or in addition to)
`/home/lumen/worktrees`. Needs a dispatch with `--add-dir /data/lumen`
(read+write).

## 2. Move archive/backup data to `/mnt/lumen-backup` — BLOCKED, out of reach

Same block: `/data/lumen`, `/scratch`, and `/mnt/lumen-backup` are all outside
this session's grant. The decision itself isn't gated (full-autonomy standing
order) — wq#2909 already recorded the equivalent call for `/scratch` as the
move target: restic-native copy + `check --read-data` before touching
anything live, `rsync -a` + verify for the plain archive tree, symlink-swap
last. The same procedure applies here with `/mnt/lumen-backup` as the target
per this ticket's FIX. Needs `--add-dir /data/lumen --add-dir
/mnt/lumen-backup` (read+write) to execute. **Do not delete anything unlisted
in a restic snapshot** (ticket instruction) — the move-then-symlink sequence
respects that; nothing is deleted until the copy is verified.

## 3. Alert at 90% — shipped here

No existing watcher covered `/data/lumen` (or any non-root mount): root has
its own 85% alarm in `bin/lumen-disk-hygiene:14`, but that script explicitly
never touches `/data/lumen` (line 8, by design — it's a reclaim script, not a
monitor) and no `fleet-audit`/generic per-mount alarm exists anywhere in this
worker's granted roots (same absence wq#2909 item 4 found).

`disk_pct_alarm.py` (this directory) is a generic, config-driven, per-mount
disk-usage-percent alarm — not a one-off for `/data/lumen`, so the next mount
that fills (`/scratch`, `/mnt/lumen-backup`) is one `disk_pct_alarm.yaml` entry
away rather than a new script:

- `disk_pct_alarm.py:40-44` (`default_config`) ships `/data/lumen` at the
  ticket's 90% raise threshold with an 85% clear (hysteresis, same shape as
  `aer_storm_alarm.py`'s `raise_above`/`clear_at_or_below`).
- `disk_pct_alarm.py:174-197` (`check`) is edge-triggered: one `aq.ingest` +
  one ntfy on raise (`disk_pct_alarm.py:102-110`), resolved with one RECOVERED
  ntfy on clear (`disk_pct_alarm.py:113-129`), matching the no-spam contract
  `aer_storm_alarm.py` (wq#2851) and `phoenix_disk_guard.py` (wq#1299) both
  use. A probe failure (missing mount, permission) carries the prior state
  forward (`disk_pct_alarm.py:164-171`, `next_state`) rather than reading as a
  false clear.
- `disk_pct_alarm.py:150-160` (`disk_usage_pct`) is the real probe
  (`shutil.disk_usage`); `check()` takes an injectable `prober` so tests never
  touch a real mount — `/data/lumen` is outside this worker's grant, so this
  is also the only way the alarm's logic could be tested at all this session.

Tests: `tests/test_disk_pct_alarm.py`, 16/16 passing (`python3 -m pytest
tests -q` from this directory) — edge-trigger raise/clear/no-spam/hysteresis/
probe-failure cases (mirroring `test_aer_storm_alarm.py`'s coverage shape),
config validation (empty mounts, missing path, inverted hysteresis), and the
real `disk_usage_pct` probe against a tmp dir and a nonexistent path (the
probe that *can* run without `/data/lumen`).

Deploy: `install.sh` (not run by this BUILD worker — no `systemctl` write
access in this sandbox, `/etc` is outside the worktree; same constraint noted
in `ops/wq1299-phoenix-tracer/install.sh`). Run as root on Lumen:
`lumen-run start --root -- bash
/home/lumen/foreman/ops/wq3171-disk-data-lumen-alert/install.sh`. Implements
the wq#1696 deploy-verify contract (`install.sh --verify`).
