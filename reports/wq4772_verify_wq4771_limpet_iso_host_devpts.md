# wq#4772 — verify-pass of wq#4771 (limpet ISO chroot teardown unmounted host devpts)

Verdict: **CONFIRM**. Author: claude-opus-5-5. Verifier: claude-sonnet-5 (this ticket).

## What wq#4771 claimed

Two PRs, both landed on `/home/lumen/foreman` main (commit `65f68871`, limpet/iso-builder)
and `/home/lumen/bin` main (commit `8ceb262` / equiv `9410083` on `fix/host-pty-probe`):

1. `limpet/iso-builder/build.sh`: stop bind-mounting the host's `/dev` and `/dev/pts`
   into the build chroot (root cause of the 3-day host PTY outage), add a WORKDIR-prefix
   guard on every unmount, reject a WORKDIR that resolves onto a host system path, and
   re-exec `all` under `unshare --mount --propagation private`.
2. `host-pty-check`: a read-only probe (devpts mounted at `/dev/pts` + `/dev/ptmx` is the
   5:2 char device, root-owned, mode 0666) wired into `lumen-secret-check`, filing an `aq`
   row on FAIL.

## What I checked

- Read `git show 65f68871` in full (all 10 hunks across build.sh, README.md,
  test_layered_remaster.py, test_live_test_ssh.py) and the new
  `test_wq4771_host_dev.py` (131 lines, 7 tests).
- Ran the full iso-builder suite post-fix: **192 passed** (71s), matching the claimed count.
- Adversarial check on the claim "7 new tests all fail on pre-fix build.sh": swapped
  `build.sh` for the `65f68871~1` revision, reran `test_wq4771_host_dev.py` — multiple
  failures (`test_all_reexecs_itself_in_a_private_mount_namespace`,
  `test_all_refuses_non_root`, etc., with the pre-fix script never calling `unshare` and
  never rejecting a non-root `all`). Restored the post-fix `build.sh` from a saved copy
  and confirmed `git diff` is clean. Confirms the probe can fail — not a vacuous test.
- Ran `/home/lumen/bin/tests/test_host_pty_check.py`: **11 passed**.
- Read `host-pty-check` and the `lumen-secret-check` integration diff: FAIL path files
  an `aq` row (dedup'd while open) independent of the ntfy-summary line; correctly wired.
- Traced every `mount`/`umount` call site in `build.sh` (not just the ones the new tests
  touch), per wq#4771's "audit every umount in the builder" remediation item:
  - `stage1b_assemble_chroot`: now uses `populate_chroot_dev` (own tmpfs + mknod'd nodes
    + `newinstance` devpts) instead of `mount --bind /dev` / `mount --bind /dev/pts`.
    `mount --make-private "$CHROOT"` runs immediately after the overlay mount and before
    any submount. Teardown loop uses the guarded `_unmount_retry`.
  - `install_live_test_ssh` (the live-layer openssh-server validation chroot, a **second,
    untested-by-the-new-suite** call site): also used to do `mount --bind /dev "$ov/dev"`
    / `mount --bind /dev/pts "$ov/dev/pts"` — same bug class, same root cause. The diff
    fixes this site too (`populate_chroot_dev "$ov"` + `mount --make-private "$ov"`).
    Confirmed via `git show -- build.sh` hunk at the old `873,900` lines.

## Finding (noted, not a contradiction)

`install_live_test_ssh`'s own inline teardown (`for m in "${masks[@]}" dev/pts dev sys
proc run; do mountpoint -q "$ov/$m" && umount "$ov/$m"; done`, around line 983) still
calls `umount` directly and was **not** touched by this diff — it does not route through
the new `_unmount_retry` WORKDIR-prefix guard. wq#4771's remediation item 1 said "audit
every umount in the builder"; this one call site still bypasses the explicit guard, and
`test_wq4771_host_dev.py` never exercises `install_live_test_ssh`'s teardown, so this gap
isn't covered by the new regression tests either.

This is **not** a live reintroduction of the reported incident: the root cause (binding
the host's real `/dev`/`/dev/pts` into the chroot) is fixed at this call site too, so
`$ov/dev/pts` is now a `newinstance` devpts — a distinct superblock the host's devpts
cannot be reached through regardless of how the unmount is guarded. A separate catch-all
(`stage_teardown` → `teardown_workdir_mounts` → `workdir_mounts` (findmnt-driven) →
`_unmount_retry`) also exists and would catch anything left under `$WORKDIR/live-chroot`
if the inline cleanup fails, so the guard isn't entirely absent for this path, just not
exercised inline. Logged here as a residual hardening gap, not grounds to contradict.

## Conclusion

Root cause (host `/dev` bind-mount under shared propagation) is eliminated at both call
sites that had it. New tests are non-vacuous (fail pre-fix, pass post-fix, independently
verified). Full iso-builder suite (192) and host-pty-check suite (11) pass. `host-pty-check`
logic (mountinfo parsing, char-device major/minor, uid/gid/mode checks) is correct and its
`lumen-secret-check` wiring files an `aq` row on FAIL as documented.

**CONFIRM wq#4771.**
