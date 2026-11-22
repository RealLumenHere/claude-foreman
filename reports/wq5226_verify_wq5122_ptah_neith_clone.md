# wq#5226 — verify wq#5122 (Ptah eMMC root cloned to Neith), PR #2698

Verdict: **CONFIRM**, no contradiction. All factual claims in
`ops/wq5122-ptah-neith-clone/README.md` that can be checked from Lumen without a fleet
grant hold. Five minor defects below. None changes the outcome; they only matter if the
scripts are re-run, since the README says they can be.

Diff under test: commit `3f398316` (4 files, +166). It is docs and an ops record only, with
no harness code. The hardware work itself was done before this PR.

## Checks run (2026-11-22 ~05:00 UTC, read-only)

| Claim | Check | Result |
|---|---|---|
| Scripts "copied verbatim" from `/scratch/clone/` | sha256 of each pair | identical: phase67 `785289d4…`, push `3f7db6fe…`, stage `b3845231…` |
| Stage: 2 passes rc=0, 87,946 files, 13.09 GB | `stage-pass{1,2}.log` | pass1:411 and pass2:53 `rc=0` (stage-pass.sh captures `RC=$?` correctly); 87,946 files; 13,092,744,816 B; 0 error/vanished lines |
| Push equals pass 2 (deviation 3) | `push-maksoor.log:270-278` | 87,946 files, 13,092,744,816 B, the same as pass 2; full rsync summary, 0 error lines |
| Backup sha256 `e791fa51…6914`, 1,247,230,076 B | `sha256sum` + `stat` | match, size match |
| Backup is the full eMMC | zstd frame-header content size | 31,268,536,320 B, the same as `lumen_state neith.hardware` eMMC size |
| `OLD=fff4af74…` correct for both sed targets | Ptah tree fstab:1, armbianEnv:5, extlinux:5; Neith `bootcfg` tar extlinux | both boards shared `fff4af74…` (same image), so the sed on Neith's restored extlinux is correct |
| pc-plus DTB, kernel, initrd exist | fake-super xattrs on staged tree | `boot/{Image,uInitrd,dtb}` are symlinks (`120777`) to the 6.18.56 files; `dtb-6.18.56…/rockchip/rk3399-roc-pc-plus.dtb` exists |
| Identity tar has everything phase67 reads | member names only | all 11 paths present, 3 host `.pub` keys |
| Phase 9: machine-id and hostkeys differ from Ptah | sha256 Ptah tree vs identity tar | machine-id, rsa/ed25519/ecdsa `.pub` and hostname all DIFF |
| dbus machine-id (runbook ph7) | staged tree | `/var/lib/dbus/machine-id` is a symlink to `/etc/machine-id`, so it is covered |
| Static MACs (runbook ph7) | netplan / NM / systemd-network / armbianEnv | none |
| Bootloader identical on both boards (ph1 gate) | `preflight/*.txt` "first 16MiB" hash | `7adf3f819fc3c09c3be9` on both; SPI hashes differ (left untouched) |
| Episodes M-26f4872182f1, M-9f4250b38fbb, M-873563654156 | `lumen_episode.py recall` | all three exist with matching titles |
| Ptah up | `curl telnet://100.78.207.56:22` | `SSH-2.0-OpenSSH_10.0p2 Debian-7+deb13u4` |
| Neith unreachable, tracked by wq#5223 | curl :22 on TS 100.108.107.111 and LAN .151 | still no answer; wq#5223 exists (`pending_review`) |

I could not re-check from here: sectors 0-32767 unchanged after the write, and the live
Phase 8 boot. Both need Maksoor or Neith access, and Neith is down. The evidence for them is
episodes M-26f4872182f1 and M-9f4250b38fbb plus `lumen_state neith.os` (root UUID 1f978c5c).

## Defects found (minor, non-blocking)

1. **`push-maksoor.sh:10` always logs `rc=0`.** `rc=$?` comes after `$(date -Is)` in the
   same word, so it reports date's exit status, not rsync's. Probe:
   `reports/wq5226_verify_wq5122_rc_probe.sh` prints `as-shipped … rc=0` and then `rc-first … rc=1`.
   The README's "push rc=0" therefore rests on a field that cannot fail. The push still looks
   successful on other evidence: the full stats summary and zero `rsync error` lines, which
   the awk filter would have kept. Fix: `RC=$?` straight after rsync, the way stage-pass.sh does it.
2. **`push-maksoor.sh` has no mount guard.** It has no `set -e`/`pipefail` and no `findmnt`
   check on `/mnt/neith-p1`. If someone re-runs it with the image unmounted, `--delete` writes
   13 GB into Maksoor's root fs. `phase67.sh:6` has the check this script lacks.
3. **`lost+found` deleted on p1.** `push-maksoor.log:272` shows `deleted files: 1 (dir: 1)`:
   the fresh mkfs `lost+found` was removed, because staging excluded it and the push used
   `--delete`. This is cosmetic, since e2fsck or `mklost+found` recreates it.
4. **`phase67.sh:69` is fragile on re-run.** Under `pipefail`, `cat a b 2>/dev/null | sort -u`
   aborts the script if either `authorized_keys` is missing. It did not fire here because both
   files exist, but it would on a target without root keys.
5. **Runbook Phase 7 random-seed reset skipped.** Ptah's `/var/lib/systemd/random-seed` went
   to Neith unchanged. The README's identity-deviation note does not list this. Low impact:
   systemd rewrites the seed on boot.

Not counted against this PR: the commit carries a `Co-authored-by: Lumen Industries` trailer.
29 of the last 30 foreman commits have the same trailer, so it comes from lumen-pr itself.
Cosmetic: the README phase table lists phase 3 after phase 4.
