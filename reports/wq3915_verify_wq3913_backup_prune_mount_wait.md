# wq#3915 — verify-pass on wq#3913 (lumen-backup-prune.service mount-wait fix)

Commit: `1ac14e63` (merged to `main` via `8945c6b7`). Author: claude-sonnet-5 ·
Verifier: claude-sonnet-5@high (fresh instance, wq#3915). Ops bundle:
`ops/wq3913-a1-backup-prune-mount-wait/` (`10-wait-for-mount.conf`,
`install.sh`, `README.md`). ISSUE-LOG row 1319.

## Claim under test

`lumen-backup-prune.service` failed at 2026-10-14 06:32Z because its
`Persistent=true` timer ran the service 9s after a 06:20:16 reboot, while
`/mnt/lumen-backup` (external USB, `/dev/sda1`) — per the commit message,
README, and ISSUE-LOG row 1319 — "took 31 more minutes to finish mounting",
so `restic forget` hit an empty mountpoint. Fix: add
`RequiresMountsFor=/mnt/lumen-backup` to `lumen-backup-prune.service` via a
`service.d` drop-in so any future early run orders after and requires that
mount.

## What I checked

1. **Diff cold** (`git show 1ac14e63`): three new files, 133 lines, no
   existing file touched. `10-wait-for-mount.conf` is a 2-line `[Unit]`
   drop-in behind a long evidence comment; `install.sh` follows the
   wq#1696 `--verify` contract (`vunit_file` byte-compare only, read-only);
   `README.md` carries the root-cause narrative and a "17.9G memory peak —
   reviewed, not a regression" section.
2. **Live deploy, byte-for-byte.** `systemctl cat lumen-backup-prune.service`
   shows `/etc/systemd/system/lumen-backup-prune.service.d/10-wait-for-mount.conf`
   installed with content identical to the source file I read via `Read`
   (same evidence comment, same `RequiresMountsFor=/mnt/lumen-backup`).
   `systemctl show lumen-backup-prune.service -p RequiresMountsFor -p After
   -p Requires` confirms the effective dependency: `Requires=` and `After=`
   both include `"mnt-lumen\x2dbackup.mount"`. `systemctl show
   'mnt-lumen\x2dbackup.mount' -p RequiredBy` confirms the reverse edge:
   `RequiredBy=lumen-backup-prune.service`.
   Could not run `install.sh --verify` itself — `bash
   ops/wq3913-a1-backup-prune-mount-wait/install.sh` isn't on the Bash
   allowlist (only `bash scripts/*` is; `harness/worker-settings.json:95`).
   Named here per the rule on refused shapes rather than retried. Reproduced
   the same byte-identity check `vunit_file` would have done via
   `systemctl cat` instead.
3. **Scope check.** `systemctl show lumen-backup-critical.service
   -p RequiresMountsFor` / same for `-important` → neither lists the backup
   mount (only the `/home/lumen` WorkingDirectory one), confirming the
   README's "scoped to prune only" claim.
4. **Unit no longer stuck.** `systemctl show lumen-backup-prune.service -p
   ActiveState -p SubState -p Result` → `inactive`/`dead`/`success` — the
   `reset-failed` the README claims actually happened and held.
   `systemctl show lumen-backup-prune.timer -p Persistent` → `yes`, matching
   the `Persistent=true` claim.
5. **Tried to break the root-cause narrative — succeeded.**
   `journalctl -b 878047020e0f41ce8ad99776eb4d6fda` (the incident boot) for
   the 06:20:16–06:51:36 window, grepped for `sda1|lumen-backup|mnt-lumen`
   and separately windowed `--since 06:45 --until 06:55`:
   - **Zero** entries for `mnt-lumen\x2dbackup.mount` anywhere in the boot —
     no attempted automatic mount, no failure, no retry.
   - `systemctl show 'mnt-lumen\x2dbackup.mount' -p WantedBy` → empty. This
     is a `noauto` fstab entry (`SourcePath=/etc/fstab`,
     `UnitFileState=generated`): nothing pulls it into `local-fs.target` at
     boot. It was never going to mount itself, ever, with or without a
     31-minute delay.
   - The actual sequence, straight from the journal:
     ```
     06:20:17 kernel: sda: sda1                         (device enumerates instantly)
     06:25/35/45:16   python3[...]: FINDING ...lumen-backup-prune.timer...fails
     06:50:23 sudo[114062]: lumen ... COMMAND=/usr/bin/ls /mnt/lumen-backup/restic-local
     06:51:35 sudo[125348]: lumen ... COMMAND=/usr/bin/mount /mnt/lumen-backup
     06:51:36 kernel: EXT4-fs (sda1): mounted filesystem ... (one second later)
     ```
     An `ls`-then-`mount` pair 72s apart, run as root via the `lumen` user,
     immediately after three recurring dead-man `FINDING` pages — this is an
     operator (human or an acting Claude session) diagnosing and manually
     mounting the drive, not a drive "finishing" an in-progress mount. No
     script in `/home/lumen/bin` references `/mnt/lumen-backup` or
     `mnt-lumen-backup` (checked via `grep -rl`), so there is no automated
     remediation path that could reframe this as a scripted-but-slow mount
     either.
   - The device itself enumerated in ~1s (06:20:17, one second after boot);
     nothing about the hardware was slow. The entire 31-minute gap is "no one
     asked systemd to mount it yet."

## Verdict

**CONTRADICT** (narrative, not the shipped unit). The deployed drop-in is
live, byte-identical to the repo, correctly scoped to `lumen-backup-prune.service`
only, and wires the dependency systemd shows in its own unit graph — none of
that is in dispute. What's wrong is the causal story the diff ships in three
places (commit message, `README.md` "Root cause", and ISSUE-LOG row 1319):
"the external USB drive... took 31 more minutes to finish mounting" describes
a passive, in-progress automatic mount racing the timer. The journal proves
there was no automatic mount attempt at all — `WantedBy=` is empty on the
mount unit, so without an explicit trigger it does not mount on its own, ever.
The 31 minutes is entirely the gap until a human (or acting agent) noticed
the dead-man pages and ran `mount /mnt/lumen-backup` by hand.

This likely does not break the remedy: `RequiresMountsFor=` is precisely the
systemd idiom for forcing on-demand activation of a `noauto` mount, so once a
boot-time run of `lumen-backup-prune.service` needs it, systemd itself should
now issue the mount — which is a *better* fix for the real problem than what
the README describes, since it replaces "wait for a human to notice three
pages" with "systemd mounts it itself." But the author never tested or
reasoned about that mechanism — they believed (and wrote into a permanent
ISSUE-LOG row that future triage reads before investigating) that this was a
timing race against a mount already in flight. That belief is false, provably
so from evidence the author had equal access to (`journalctl -b`, which I ran
with no path broader than the incident's own boot ID), and it is now the
fleet's recorded explanation for this failure class. Per protocol, contradiction
is a judgment trigger for opus-5@high; re-verifying the `RequiresMountsFor`
on-demand-mount mechanism against a real noauto-trigger scenario (without
rebooting Lumen or unmounting the live backup drive, which the verify-only
scope here correctly avoided) belongs to that pass, as does deciding how to
correct ISSUE-LOG row 1319's cause text (logged as follow-up row 1321,
`kb/ISSUE-LOG.md`, status `open`).

## Non-blocking

- `README.md`'s "17.9G memory peak" analysis (cache dir pinned off tmpfs,
  consistent with the 2026-10-11 clean run's own 17G peak) isn't something I
  could independently re-derive — `/home/lumen/services/backup/` is outside
  this worktree's allowed paths — but nothing in what I *could* check
  contradicts it, and it's an orthogonal claim to the mount-timing one.
