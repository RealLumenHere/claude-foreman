# wq#3913 — lumen-backup-prune.service FAILED 2026-10-14 06:32Z (errors=1)

## Root cause

The box rebooted at 06:20:16 UTC on 2026-10-14 (prior boot's last journal
entry: 06:19:08 UTC; new boot ID `878047020e0f41ce8ad99776eb4d6fda`).
`lumen-backup-prune.service` is `RequiredBy=`/`TriggeredBy=`
`lumen-backup-prune.timer`, which carries `Persistent=true`; the timer
started at 06:20:24 and immediately ran the service at 06:20:25 — 9 seconds
after kernel boot.

`/mnt/lumen-backup` (external USB drive, `/dev/sda1`, confirmed by `df -h`
and kernel USB enumeration lines at 06:20:16 in `journalctl -b 0`) did not
finish mounting until **06:51:36 UTC** — 31 minutes later
(`systemctl status 'mnt-lumen\x2dbackup.mount'` → `Active: active (mounted)
since Wed 2026-10-14 06:51:36 UTC`).

`backup-prune.sh`'s first loop iteration is `REPO_LOCAL=/mnt/lumen-backup/restic-local`
(`/home/lumen/services/backup/env.sh:5`). At 06:20:25 that path was an empty
mountpoint, so both `restic forget --tag critical` and `restic forget --tag
important` failed:

```
Fatal: repository does not exist: unable to open config file: stat /mnt/lumen-backup/restic-local/config: no such file or directory
```

(full detail in `/home/lumen/services/backup/logs/prune-20261014.log`, not
visible in `journalctl` because `backup-prune.sh` redirects each restic
call's stdout+stderr straight to `$LOG` via `>>"$LOG" 2>&1`, only the `log()`
summary lines reach the unit's own stdout/journal).

The second loop iteration, `REPO_MIRROR=/data/lumen/backups` (a local disk,
always available), ran fine and logged `OK`. One repo failed + one repo
succeeded -> `ERRORS=1` -> `exit 1` -> systemd oneshot failure -> unit stuck
`failed`.

The 2026-10-11 run (previous boot, no reboot race) completed `errors=0` —
this is intermittent, boot-timing-dependent, not a steady-state break.

## Fix

`10-wait-for-mount.conf` adds `RequiresMountsFor=/mnt/lumen-backup` to
`lumen-backup-prune.service`. systemd expands that into both an `After=` and
a `Requires=` on the mount unit covering that path
(`mnt-lumen\x2dbackup.mount`), so any future boot-time (or otherwise
early) run of this unit orders after and requires that the drive is actually
mounted first, instead of racing it and hard-failing on a transient empty
mountpoint.

Scoped to `lumen-backup-prune.service` only (this ticket), not the sibling
`lumen-backup-critical.service`/`lumen-backup-important.service`, which also
read `REPO_LOCAL` — those are hourly/daily units so a boot-time miss is
retried within the hour/day rather than waiting a full week; out of scope
here.

## Memory peak (17.9G) — reviewed, not a regression

The `17.9G memory peak` the ticket flags is on the `/data/lumen/backups`
(`REPO_MIRROR`) prune leg, which is the one that actually ran its `forget` +
`prune` to completion (12min wall clock after the first repo's restic calls
failed fast). `env.sh:14` already pins `RESTIC_CACHE_DIR=/scratch/restic-cache`
off tmpfs (fix from 2026-07-19, per that file's own comment, for exactly this
class of memory-pressure incident) — restic's `prune` on a multi-hundred-
snapshot local repo rebuilding/repacking its index is memory-proportional to
repo+index size, and 17.9G is in line with the prior clean run's 3h26m CPU /
17G peak on 2026-10-11 (same journal excerpt). No swallowed-stderr or
resource-leak signal found; not changing cache/resource limits for this
ticket since nothing here points to a regression, only restic's normal
prune cost on this repo's current size.

## Install

```
/home/lumen/bin/lumen-run start --root --name wq3913-install -- \
  bash /home/lumen/foreman/ops/wq3913-a1-backup-prune-mount-wait/install.sh
```

Verify — read-only, wq#1696 contract handler:

```
bash ops/wq3913-a1-backup-prune-mount-wait/install.sh --verify   # exit 0 = live
systemctl cat lumen-backup-prune.service | grep RequiresMountsFor
```

Rollback command is printed by `install.sh` itself.

## Unit reset

```
systemctl reset-failed lumen-backup-prune.service
```

clears the `failed` state left by the 06:32:30 exit; the next real run (next
Sunday, or any boot-time catch-up) is now ordered after the mount.
