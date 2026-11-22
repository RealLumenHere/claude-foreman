# wq#5247 — failed units split from wq#5245

Three units the wq#4999 aq/LRA sweep found failed, left after wq#5245 fixed
`lumen-backup-critical`. This bundle diagnoses each one and applies only the fix whose
evidence it finds on the live node; anything else stops with the evidence printed.

```
bash install.sh --verify            # read-only, lumen user: exit 0 when none of the three is failed
bash install.sh --diagnose [NODE]   # evidence only, no change
bash install.sh [NODE]              # diagnose + fix (Lumen part re-execs under sudo)
```

## Why this is a bundle and not a live fix
The dispatching worker (fworker wq5247-a1) had no reach: `fleet-exec run maksoor|bes`
→ `DENY [grant:grant_missing_env]` (no fleet-exec grant on this dispatch), raw `ssh` not
on the allowlist, the root-escalation verb denied by guard.sh, and `/etc/audit` is
`drwxr-x--- root` so even `stat /etc/audit/rules.d` is refused to the lumen uid. The
apply is root-only work on three hosts; it rides the `[ops-root-apply]` follow-up.

## 1. Lumen `audit-rules.service`
Evidence (lumen uid, read-only): `ExecStart=/usr/sbin/augenrules --load`; failed
2026-11-12 23:35:38Z (boot), `status=1/FAILURE`; `auditd.service` active since the same
second. Journal starts 2026-11-15 04:05Z (`journalctl --list-boots`: one boot, first entry
11-15), so the error text is gone. Only `auditd` ships a rules file
(`/var/lib/dpkg/info/auditd.list`: `/etc/audit/rules.d/audit.rules`); auditd last
upgraded 2026-10-14 to 1:4.1.2-1ubuntu0.1 (`/var/log/dpkg.log`). No fleet repo,
ISSUE-LOG row or ticket references an `/etc/audit/rules.d` file, so whatever extra rule
is there was placed by hand or by an unrecorded run. **Root cause: not determinable
without root.**

`augenrules --load` = concatenate `rules.d/*.rules` into `/etc/audit/audit.rules`, then
`auditctl -R` it (`/usr/sbin/augenrules:41-46,147`); auditctl names every refused rule as
`There was an error in line N of FILE` (string in `/usr/sbin/auditctl`). `lumen-audit.sh`:
- reloads; if that succeeds, the 11-12 failure was boot-time only (e.g. a watched path
  that appeared after `local-fs.target`) and it just clears the failed state;
- otherwise maps each refused line back to its `rules.d` file. If the rule depends on a
  path that does not exist (`-w PATH`, `dir=`, `path=` — the most common cause, and the
  SUSE KB "augenrules fails to load rules for users' home directories" case), it backs
  the file up (`.bak.<ts>_wq5247`) and comments the line out with a `# wq5247 disabled`
  prefix, then reloads;
- any other refused rule: prints it with its source file and stops (`UNFIXED`).

## 2. maksoor `max-brightness.service`
Created by wq#3637 on 2026-10-08 for the laptop panel (`fleet-intel/history/maksoor.md:36`:
"brightness 7142/7142"; first boot found it "hardcoded 4285"). `lumen_state` fact
`maksoor.hardware` (since 2026-11-16): "i5-12500 … Intel UHD 770. NOT a Yoga 720 — this
is a desktop board in new chassis (motherboard swapped in orch-14)". A desktop board has
no internal panel, so the `/sys/class/backlight/<dev>` the unit writes no longer exists.
**Probable root cause; `remote-maksoor.sh` confirms it before acting**: it extracts the
backlight paths from `systemctl cat`, and only if the unit is failed and a named device
(or, if none is named, every backlight device) is absent does it install
`max-brightness.service.d/wq5247-require-backlight.conf` with
`ConditionPathExists=<dev>`. The unit then skips as inactive instead of failing, and runs
unchanged if a panel ever returns — reversible by deleting one file, no mask (a mask
cannot be applied over a unit file in `/etc/systemd/system` anyway). Device present →
`UNFIXED`, journal printed.

## 3. bes `grub-initrd-fallback.service`
Stock Ubuntu unit: two `grub-editenv /boot/grub/grubenv unset …` calls. The same unit
failed on Thor on 2026-08-08 because grubenv was corrupt ("invalid environment block";
`agents/work-queue/reports/fleet_update_20260808.md:55-58`), fixed by backing it up and
`grub-editenv create`. `remote-bes.sh` checks the signature (`grubenv_state`) and
`grub-editenv list`; only on corruption does it back up to
`grubenv.corrupt.bak.<ts>` and recreate. Signature ok and `list` rc=0 → the failure was
boot-time only: reset-failed + restart the unit (its own two `unset` calls), file never
touched; still failing → `UNFIXED`, journal printed. Any other state (bad size, absent,
list error) → `UNFIXED`.

**wq#5332 live diagnosis (root, 2026-11-22):** journal `invalid environment block` at
2026-11-12 23:27:49 (boot), but grubenv's mtime is that same minute and it is valid now
(signature ok, `grub-editenv list` rc=0; `/` rw ext4, 7% used). The file was rewritten
during boot after the unit read it, so the "readable grubenv" branch above was added; the
first bundle refused that case as `UNFIXED`.

**wq#5337 (from verify wq#5334):**
- *Unreadable is not corrupt.* `remote-bes.sh` runs as the unescalated `bes` ssh user;
  `grubenv_state` used to read the file as that user, so a permission failure came back
  as `bad-header` and the script would back up and `grub-editenv create` a healthy env.
  Reads (and `grub-editenv list`) now go through `sudo -n` when it works, and a read
  failure is its own state, `unreadable` → `UNFIXED`, file untouched.
- *The likely root cause is Ubuntu LP#2156423, not corruption.* The packaged
  `grub-initrd-fallback.service` ordered `After=grub-common.service`, but that unit was
  renamed `grub2-common.service`, so both units rewrite grubenv in parallel at boot and
  one reads a half-written block. Fixed upstream in grub2 for Ubuntu 26.04.1
  ("grub-initrd-fallback.service: After grub2-common.service"); Lumen's unit already has
  it. `remote-bes.sh` reads `systemctl cat` (`initrd_fallback_order`): stale order while
  `grub2-common.service` exists → installs the drop-in
  `/etc/systemd/system/grub-initrd-fallback.service.d/wq5337-lp2156423.conf`
  (`After=grub2-common.service`) + daemon-reload, whether or not the unit is failed right
  now, and prints the real fix: `sudo apt-get install --only-upgrade grub2-common`
  (the drop-in is redundant, not harmful, afterwards).

## Files
- `install.sh` — entry point + `--verify` (ops/_verify.sh contract).
- `lib.sh` — the decision helpers (pure; `audit_disable_rule` edits only its argument).
- `lumen-audit.sh`, `remote-maksoor.sh`, `remote-bes.sh` — one per unit; the remote ones
  are piped with `lib.sh` over `ssh … bash -s`.
- `test_failed_units.py` — 22 tests, stubs on PATH, fixtures only; each branch above
  (fix and refuse) is covered.
