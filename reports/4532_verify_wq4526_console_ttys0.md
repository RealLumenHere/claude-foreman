# wq#4532 verify: wq#4526 console=ttyS0 serial console kernel params

- Author: claude-sonnet-5
- Verifier: claude-sonnet-5@high (fresh instance)
- PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2382 (gitea MCP read denied
  both this attempt and the prior one -- verified instead from the commit already in
  this worktree's history: `ce0fd9fb feat(limpet-iso): wq4526 add console=ttyS0 serial
  console to kernel params`, which matches the PR title/description)

## Diff under test

- `limpet/iso-builder/set_grub_cmdline.py` (new, 48 lines): idempotently rewrites
  `GRUB_CMDLINE_LINUX="console=ttyS0,115200n8"` into `/etc/default/grub`, replacing an
  existing (commented or live) line in place or appending one.
- `limpet/iso-builder/build.sh`: new `stage10_configure_serial_console` -- calls
  `set_grub_cmdline.py` against `$CHROOT/etc/default/grub` then `chroot "$CHROOT"
  update-grub`; inserted into `STAGES=()` between `stage9_apply_branding` and
  `stage11_build_hybrid_iso` (i.e. before the layer is packed and before
  `stage12_qemu_test` boots it).
- `limpet/iso-builder/test_set_grub_cmdline.py` (new, 4 tests): append-when-absent,
  replace-existing, replace-commented, idempotent-rerun.
- `README.md`: documents the new stage.

## What I checked

1. Ran the shipped test suite: `pytest test_set_grub_cmdline.py` -- 4/4 pass.
2. Adversarial probes outside the shipped tests' input population (wq#2630 --
   falsifiability): a file with no trailing newline before append, a file with two
   pre-existing `GRUB_CMDLINE_LINUX=` lines, a line with a trailing inline comment, an
   empty-string input, and a sibling `GRUB_CMDLINE_LINUX_DEFAULT=` line (checking the
   regex doesn't false-positive-match the longer variable name). All five behaved
   correctly -- in particular the sibling-var probe confirms `GRUB_CMDLINE_LINUX=` is
   matched as a full token (regex requires the literal `=` immediately after
   `GRUB_CMDLINE_LINUX`, so `_DEFAULT` lines are never touched).
3. Checked `STAGES=()` ordering in build.sh: stage10 runs after stage9 (branding,
   which never touches `GRUB_CMDLINE_LINUX`, only its own theme files) and before
   stage11 (packs chroot's upper/ into squashfs) and stage12 (`qemu-system-x86_64
   ... -serial "file:$log_file"`). QEMU's default first `-serial` device is the
   guest's first UART, which is exactly `ttyS0` -- so the chosen device name matches
   what stage12's test harness actually attaches, closing the loop the PR describes
   (empty `limpet-serial.log`).
4. Confirmed `HERE` and `CHROOT` (used by the new stage) are defined at build.sh's
   top level (`HERE=...` line 21, `CHROOT="$WORKDIR/chroot"` line 26) -- not
   introduced or redefined by this diff.
5. `set -euo pipefail` is active for the whole script (line 19); the new stage's
   un-guarded `chroot "$CHROOT" update-grub` matches the existing convention elsewhere
   in build.sh (e.g. the pre-existing `chroot "$CHROOT" apt-get autoremove -y`) --
   failure aborts the build rather than silently continuing, consistent with sibling
   stages, not a regression introduced here.
6. `python3 -I` isolated-mode invocation of the script in build.sh matches this
   project's convention for running an internal tool against untrusted/chroot-rooted
   paths.

## Verdict: CONFIRM

No functional defect found. The fix is correctly scoped (only `GRUB_CMDLINE_LINUX`,
not `_DEFAULT`), idempotent under multiple reruns and malformed/empty input, ordered
correctly in the stage pipeline, and the device name (`ttyS0`) matches the QEMU test
harness it's meant to unblock. Tests shipped with the PR pass, and additional
probes outside the shipped tests' input population did not surface a regression.
