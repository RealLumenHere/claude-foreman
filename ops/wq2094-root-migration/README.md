# wq#2094 — move bulk data off root onto /scratch

Root (466G LV) was 75% on 2026-09-19 with `/scratch` (md1) 14% used. `migrate.py`
moves one directory at a time with a bind mount, so every path keeps working.

`migrate.py plan` (read-only, runs as any user) on 2026-09-19: the seven
`/home/lumen` moves below total ~119G, which takes root from 72% to ~46%
without touching docker. Docker's size is only visible as root, so the real
figure is lower still.

| move | src | size | services stopped |
|---|---|---|---|
| docker | `/var/lib/docker` | root-only | `docker.socket`, `docker.service` |
| gocheckin | `~/gocheckin` | 22G | — |
| bets | `~/bets` | 16G | — |
| re | `~/re` | 8G | — |
| sandbox | `~/sandbox` | 7G | — |
| simpill-scratch-preserved | `~/simpill-scratch-preserved` | 4G | — |
| esphome | `~/esphome` | 4G | — |
| projects | `~/projects` | 58G | — (refuses if any process has its cwd inside) |

## Per move

`preflight` (root, `/scratch` mounted on ext4/xfs/btrfs, ≥110% of size free, no
process cwd under src, docker root really is `/var/lib/docker`) → rsync
warm-up with services up → stop services → final rsync → zero-diff dry-run
verify → `src` renamed to `src.pre-wq2094` (**the backup; nothing is deleted**)
→ `/etc/fstab` bind line (`nofail`, `x-systemd.requires-mounts-for`) + a
`RequiresMountsFor=<src>` drop-in on each stopped unit → `mount --bind` →
start services and confirm (docker: every previously running container is back
within 180s). Any failure after the stop unwinds and restarts the services.

Root space is freed only by `finalize`, once the service is confirmed healthy.
`rollback` (before `finalize`) syncs writes made on the array back and restores
the original directory.

## Run

Privileged, detached (repo convention, see `ops/wq2092-cert-watch`):

```
python3 ops/wq2094-root-migration/migrate.py plan
lumen-run start --root -- python3 /home/lumen/foreman/ops/wq2094-root-migration/migrate.py move gocheckin --execute
# ...confirm, then free root:
lumen-run start --root -- python3 /home/lumen/foreman/ops/wq2094-root-migration/migrate.py finalize gocheckin --execute
```

Without `--execute`, `move`/`finalize`/`rollback` print the steps and touch
nothing. Suggested order: cold dirs first (gocheckin, bets, re, sandbox,
simpill-scratch-preserved, esphome), then docker, then projects while no
worker is running. Run docker via a detached root job, never foreground.

Not done by this PR: no live data has been moved. The worker that wrote this
cannot stop services or write to `/scratch`; `plan` is the only command run
against the live host.

## Tests

`python3 -m unittest discover -s ops/wq2094-root-migration` — real rsync on
temp trees with mount/systemctl faked: full move + finalize, dry run, mount
failure unwind, rollback keeping post-move writes, busy-cwd refusal, fstab
idempotence.
