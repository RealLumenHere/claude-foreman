# wq#3946 — Limpet ISO build pipeline

`build.sh` assembles a Xubuntu 26.04 base + every Limpet component into one
hybrid BIOS+UEFI bootable ISO. Run `./build.sh --help` for the stage list and
env vars, or `./build.sh all` end to end as root on a build host with real
network egress, `xorriso`/`squashfs-tools`/`qemu-system-x86_64`, overlayfs +
loop-device support, and ~15 GB free.

## Layered squashfs (wq#4077)

The 26.04 ISO has no `casper/filesystem.squashfs`. It stacks `minimal` ->
`minimal.standard` -> `minimal.standard.live`, and `casper/install-sources.yaml`
(type `fsimage-layered`) marks `minimal.standard` as the default install source.
Limpet content goes into that layer, because it's the one the installer copies to disk; the
`.live` layer only exists in the live session. `iso_layers.py target-layer`
reads the choice from the yaml; set `LIMPET_ISO_LAYER` to override it.

- `stage1b_assemble_chroot` loop-mounts the parent layers read-only, unsquashes
  the target layer to `layers/upper/`, and mounts an overlayfs of the two at
  `$CHROOT` (plus proc/sys/dev/dev/pts, a tmpfs `/run` holding the resolver,
  and a `policy-rc.d`). Every later stage's writes, including purge whiteouts,
  land in `upper/`. livecd-rootfs `lb_binary_layered` builds the layers the
  same way.
- `stage11_build_hybrid_iso` regenerates `<layer>.manifest.full`, the delta
  `<layer>.manifest`, `<layer>.size` and the `install-sources.yaml` size the
  same way livecd-rootfs does. It then unmounts, packs `upper/` with the
  original compression and block size, rebrands the ISO's `boot/grub/grub.cfg` +
  `loopback.cfg` ("Try or Install Limpet") and adds `console=tty0
  console=ttyS0,115200n8` to every `linux /casper/vmlinuz` line (wq#4534 --
  otherwise stage12's QEMU boot test, which boots this ISO directly, got an
  empty serial log) and refreshes `md5sum.txt`. Finally it writes a copy of the base ISO with only
  those files mapped in and the base's El Torito/MBR/GPT boot records replayed
  (`xorriso -boot_image any replay`). The base ISO's kernel/initrd are reused.
  grub-mkrescue is no longer used.
- Not handled: `minimal.standard.live.manifest*` still lists the stock live
  layer's packages. That layer's own dpkg db also shadows the target layer's
  in the live session, so packages installed by stages 4/9 show their files
  there but don't appear in `dpkg -l`. The installed system is unaffected.
  `upper/` is not rsync-deduplicated the way livecd-rootfs does it, so the
  repacked layer can be slightly larger than it needs to be.
- Which menu entries the ISO should offer (deployment-spec's "Try Limpet Live"
  default vs. the install-only GRUB decision recorded in the 2026-10-16 audit)
  is still undecided. Only the stock entries are rebranded.

## The ticket cites a spec file that does not exist

wq#3946 says the ISO build script's integration point is
"install-wizard-spec.md section 6". Repo-wide grep finds no such file — and
this is not new: wq#3941 hit and logged the identical gap
(`kb/ISSUE-LOG.md` row 1327, 2026-10-14, "ticket cites
'install-wizard-spec.md section 6' as the ISO build script integration point,
but no file of that name... exists anywhere in the repo"). Per the fleet's
full-autonomy doctrine this is not a Frank-gate (no physical act is needed),
so this script was written from the artifacts that *do* exist instead of
blocking on one that doesn't:

- `docs/desktop-spec.md` — start-bar panel XML, genmon scripts, `.desktop`
  launchers (materialized verbatim under `overlay/`).
- `docs/branding-spec.md` + `design/limpet/brand/tokens.json` — LightDM
  greeter config, terminal mark, color tokens (`overlay/etc/lightdm/`,
  `gen_motd.py`).
- `limpet/tool-catalog.json` — RE tool packaging (`gen_tool_packages.py`).
- `limpet/hardware-book/`, `limpet/hardware.d/` — hardware profile book and
  target device database (stages 5/6/10).
- `limpet/hardware.d/build_iso_drivers.py` (wq#3941's own hook point) —
  driver bundle (stage 7).
- `research/wq3921_soc_exploit_seed.json` + companion doc — exploit database
  snapshot (stage 8).

## Interpretation decisions this script makes (none guessed silently)

- **"Installer wizard"** (task 3) maps to the **Target Device Wizard**
  (`docs/target-wizard-spec.md`, the `limpet-wizard` launcher) — the only
  wizard that exists under the Limpet project. No OS-level installer UI
  (ubiquity/calamares equivalent) is named by any spec or exists in the repo.
- **"Precheck service"** (task 3) has no implementation or spec anywhere in
  the repo under that name. `build.sh stage3` logs this and skips it rather
  than fabricating a service.
- **Bloat removal list** (task 2, `bloat-removal.list`) has no
  "section 6.1" source (same missing-spec issue above) — it's this ticket's
  own reasoned call (drop office/games/print/media-player packages an
  appliance that boots to the Limpet panel never needs), documented inline.
- **Greeter theme packages** (task 9, wq#4073): the greeter conf
  `overlay/etc/lightdm/lightdm-gtk-greeter.conf` names Papirus-Dark /
  Greybird-dark / Inter, but nothing installed them — LightDM would have booted
  with `icon-theme-name` pointed at a theme absent from the chroot. `stage9`
  now installs `papirus-icon-theme greybird-gtk-theme fonts-inter`
  (`docs/branding-spec.md:160-161`, `:126`), and `xubuntu-icon-theme` is no
  longer purged by `bloat-removal.list` (it was listed under the "games"
  comment; it is an icon theme). Guarded by
  `test_build_theme_packages.py`.
- **RE tool packaging** (task 4): `tool-catalog.json` records no
  apt/pip/cargo package name field. `gen_tool_packages.py` resolves one the
  way a human would — `dpkg -S <verified-path>` on the build host — and
  reports a tool unresolved rather than guessing a package name. Only
  `core`/`vetted` trust tiers are baked in; `community` stays out until
  promoted (mirrors `limpet/registry/import_catalog.py`'s existing
  skip-by-design behavior).
- **GRUB/Plymouth branding** (task 9): originally deferred because no theme
  assets existed. wq#3948 created them (`design/limpet/brand/themes/grub/`,
  `.../plymouth/`) in the committed, WCAG-AA token palette, so `build.sh
  stage9` now installs a graphical GRUB theme (via `set_grub_theme.py`) and an
  animated Plymouth splash instead of shipping text-mode/unthemed.
- **Serial console** (wq#4526, wq#4534): `limpet-serial.log` came back empty
  from QEMU boots because nothing told the kernel to print to a serial port.
  `stage10` sets `GRUB_CMDLINE_LINUX="console=tty0 console=ttyS0,115200n8"`
  in the chroot's `/etc/default/grub` (via `set_grub_cmdline.py`) and runs
  `update-grub` before stage11 packs the layer -- that fixes the *installed*
  system's grub, not the live ISO's own boot menu. `stage12_qemu_test` boots
  the ISO directly (`-cdrom`, never installed), so wq#4526 alone still left
  the serial log empty; wq#4534 closes that gap in `stage11` by having
  `iso_layers.py grub-cfg` add the same console args (tty0 first, ttyS0 last
  so it stays primary) to `boot/grub/grub.cfg` + `loopback.cfg`'s
  `linux /casper/vmlinuz` lines before the `---` separator, enabling
  serial-based automated boot testing without a VNC/QMP screenshot
  dependency. That still left the log near-empty (wq#4564): both files ship
  `quiet splash` after the `---`, and `quiet` lowers `console_loglevel` for
  every console, ttyS0 included, so the kernel/systemd print almost nothing
  regardless of where it's routed. **Decision (wq#4564, no Frank-gate):**
  drop `quiet`, keep `splash` -- Plymouth only owns the tty0 framebuffer, it
  never touched what printk sends to ttyS0, so dropping `splash` instead
  would cost the wq#3948 graphical boot branding for no serial-log gain --
  and add `loglevel=7 systemd.show_status=1` where `quiet` was, which wins
  because GRUB's `linux` line is parsed left to right and overrides any
  earlier/default loglevel. `iso_layers.add_serial_console` now rewrites
  both halves of the line in one pass.
- **Hardware profile book is 5 profiles, not 200+** (tasks 5/10): the ticket's
  "200+" is a target the upstream Tier-1/2 tickets haven't reached yet
  (`docs/hardware-tiers-spec.md` so far only has the AMD tier complete).
  `stage5_bundle_hardware_book` globs `limpet/hardware-book/**/*.toml`
  dynamically — it bundles whatever exists and needs no change as more land.

## What this script does NOT do (and why that's not a shortcut)

- **Does not fetch the base ISO.** An unattended multi-GB download with no
  checksum pinned anywhere in this repo is a guess, not a verified input.
  `stage1` requires the caller to supply `LIMPET_BASE_ISO` +
  `LIMPET_BASE_ISO_SHA256`, rejects a 0-byte `LIMPET_BASE_ISO` outright
  (wq#4008), and verifies the hash before extracting. If you need to
  populate `LIMPET_BASE_ISO`, use `fetch_base_iso.sh <version> <dest-dir>`
  (wq#4008) — it only writes `<dest-dir>/.xubuntu_download_complete` after
  every artifact it fetched is confirmed non-empty and checksum-verified
  against `SHA256SUMS`, so a 404/short-read mid-release-window can't produce
  a false "complete" marker the way an earlier ad hoc fetch did.
- **Only builds DKMS modules that are pinned.** See "Driver bundle hook" below;
  as of wq#4093's catalog fix, the catalog has zero `dkms = true` devices (every
  cataloged device is in-kernel), so there is currently nothing for this branch to build.
- **stage1 -> 1b -> 7 (driver hook) has now been run end to end as root against
  the real ISO (wq#4093).** `./build.sh stage1_fetch_base_iso`, then
  `stage1b_assemble_chroot`, then `LIMPET_DRIVER_HOOK_RUN=1 ./build.sh
  stage7_bundle_driver_bundle`, run as root on this host (`lumen`) against the
  pinned `xubuntu-26.04-desktop-amd64.iso`: extract succeeded, the
  `minimal.standard` overlay mounted cleanly, the in-chroot installer ran
  `apt-get install` for real (network + DNS resolution through the bind-mounted
  resolver worked), `90-limpet-hardware.rules` landed in
  `etc/udev/rules.d/`, and `driver_hook.py verify-installed` reported
  `{"rules": true, "hardware_d": 12, "plan": "7.0.0-14-generic", "ok": true}`.
  `linux-firmware` was already present on the base image (metapackage, pulling
  in `linux-firmware-realtek`, which carries `rtw88/rtw8822b_fw.bin.zst` --
  confirming the wq#4093 catalog fix below); `sigrok-firmware-fx2lafw` was a
  fresh install. All chroot mounts (overlay, proc, sys, dev, dev/pts, run,
  the lower squashfs loop mount) were unmounted afterward with no mounts left
  under `.build`. **Still untested:** the DKMS build branch in
  `chroot_script()` (`git clone`/`dkms add`/`dkms install`) -- the catalog now
  has zero `dkms = true` devices, so nothing exercises it. `test_layered_remaster.py`
  covers stage1 -> 1b -> 6 -> 11 (mksquashfs/unsquashfs/xorriso/dpkg-query) on a
  small synthetic three-layer ISO (`fixtures/xubuntu-26.04/`), with only
  mount/umount/mountpoint/chroot faked; boot-record replay on the real ISO is
  covered by scratch/limpet-iso/test_remaster.sh (wq#4077 evidence).
- **Base image: still 26.04, not 26.04.1 (wq#4093 decision).** Whether a
  26.04.1 point release exists and should replace the pinned 26.04 base was in
  scope for wq#4093, but every outbound search engine (SearXNG's brave/
  duckduckgo/google-cse/startpage backends) was down for this run ("too many
  requests"/CAPTCHA, no `BRAVE_API_KEY` fallback -- wq#2595), so there was no
  verified way to confirm a 26.04.1 release exists, let alone fetch its ISO
  and published sha256. Guessing a version/checksum is exactly what `stage1`
  refuses to do (see above), so the pin stays on 26.04 (`kernel.pin`,
  `BASE_ISO_RELEASE=26.04 (20260423.1)`) rather than swapping blind. Re-run
  this decision once search is reachable again or a checksum-verified
  26.04.1 ISO is supplied directly -- note `kernel.pin`'s own warning that a
  base swap means re-verifying every in-kernel claim in `dkms-sources.pin`.

## Driver bundle hook (wq#3980)

`driver_hook/` is the stage7 hook that consumes the wq#3941 bundle. It is runner-agnostic
(needs only a chroot dir), so the same code can sit behind live-build/mkosi later.

- `kernel.pin` pins the base ISO (name + sha256, verified) and the target kernel ABI
  (`7.0.0-14-generic`, read from the real ISO's `casper/vmlinuz`; its `pool/` carries the
  matching headers + modules). Note the installer picks the kernel (`kernel: default:
  linux-generic` in `install-sources.yaml`); the squashfs layers carry none.
- `dkms-sources.pin` pins DKMS sources by URL + full commit, or records an explicit `skip`
  with a reason. A catalog `dkms=true` entry with no line makes the hook exit 4.
  `rtl8812bu` is `skip`ped: the in-kernel `rtw88_8822bu` claims `0bda:b812` (verified from
  the `.ko` in the pinned ABI's modules deb and on the running 7.0.0-38 kernel); the
  catalog's `dkms_source` URL is a 404. **wq#4093**: the catalog entry itself (
  `limpet/hardware.d/10-network-adapters.toml`) was corrected to match this pin --
  `dkms = false`, `firmware_package = "linux-firmware"` (the chip's firmware is
  `rtw88/rtw8822b_fw.bin`, shipped by `linux-firmware`, not by the dead DKMS fork). The
  catalog previously still said `dkms = true` with the dead URL, so `dkms_targets()` kept
  emitting it as a (skipped) build target and its firmware was mis-classified as
  `bundled:` (i.e. "provided by the DKMS build step") -- a step that was never actually
  running. The catalog is now the only source of truth and matches the pin: zero
  `dkms = true` devices anywhere in `limpet/hardware.d/*.toml`.
- Firmware refs are classified: plain names -> apt in the chroot; `bundled:` -> provided by
  another component; `proprietary:` (Xilinx EULA firmware) -> never bundled, sideload only.
- Exit 3 if the chroot already carries a kernel other than the pin.
- `LIMPET_DRIVER_HOOK_RUN=1` runs the generated in-chroot installer (root + network).
  **wq#4093: run for real** -- see "What this script does NOT do" above for the full
  stage1->1b->7 run and its results. Apt-firmware branch confirmed working; DKMS build
  branch still untested (no catalog entry currently needs it).
- Wizard Phase 4 can call `driver_hook.py verify-installed` (JSON, exit 5 on failure).
  **wq#4093 -- udev naming conflict resolved**: the ticket that raised this
  (install-wizard-spec "says" `90-limpet.rules`) cites the same nonexistent spec file as
  the "ticket cites a spec file that does not exist" section above -- repo-wide grep finds
  `90-limpet.rules` nowhere except this note itself. `90-limpet-hardware.rules` is the only
  name with any real implementation (generated by `build_iso_drivers.py`, written by
  `driver_hook.py install`, asserted by both test suites) and stays canonical.

## wq#4456 — the `build-limpet.sh` wrapper is a stale, out-of-tree copy

A production run (`/home/lumen/scratch/limpet-4449-rebuild2.log`, invoked via
`/home/lumen/projects/limpet/iso-build/build-limpet.sh`) produced a 4.8G ISO against a
fresh `upper/`, versus 6.3G from an older hand-built `upper/` on the same host. Root
cause is two independent gaps, both in that wrapper, **not** in this script:

1. **Wrong `LIMPET_ROOT`.** The wrapper computes `LIMPET_ROOT="$HERE/.."` from its own
   location, `/home/lumen/projects/limpet/iso-build/`, i.e. `/home/lumen/projects/limpet`
   — a directory that is not this repo and not a checkout of it (confirmed: it ships a
   different, older `install-to-chroot.sh` with a `src/limpet/`/`systemd/`/`xfce/`/`data/`
   layout that doesn't exist anywhere under `limpet/` here). It has no
   `hardware-book/`, `hardware.d/`, `docs/`, or `design/limpet/brand/`, so stages 5, 6, 7
   and 9 all fail with "No such file or directory" the moment they run against it
   (`cp`/`python3` can't stat their inputs) — `build.sh`'s own `fail`-on-missing-chroot
   guards don't catch this because the inputs these stages read live under `LIMPET_ROOT`/
   `REPO_ROOT`, not `$CHROOT`.
2. **Stages 4 and 8 are never invoked.** The wrapper calls `install-to-chroot.sh` in place
   of `stage3_inject_limpet_packages`, then loops only
   `stage5_bundle_hardware_book stage6_bundle_target_device_db stage7_bundle_driver_bundle
   stage9_apply_branding` — `stage4_package_re_tools` (RE tool packaging, including the
   cargo/pip tool builds in stage4b/4c) and `stage8_bundle_exploit_db` never run at all.
   That's exactly the content an older hand-built `upper/` would carry if it predated this
   script's stage4/8 additions (or if someone built it by hand without them) and a fresh
   one won't: the likely source of most of the 1.5G gap.

**Decision (wq#4456, no Frank-gate — this is a reachable engineering call, not a physical
act):** the canonical pipeline is this script's own `STAGES` array below (`./build.sh
all`, stage1 through stage12) run from a real checkout of this repo, not a hand-maintained
subset re-listed in a copy living outside it. `build-limpet.sh` and
`/home/lumen/projects/limpet/iso-build/` are outside this repo and outside every worker's
allowed directories (confirmed independently by wq#3950's runbook, which hit the same
boundary over the plug-test kit it left there), so this ticket could not edit that wrapper
directly. What *is* fixed here: `stage1b_assemble_chroot` now carries its own EUID guard
and stale-`upper/`-ownership check (previously only in the external wrapper, so running
`build.sh` directly — which this README has always said is supported — silently skipped
both). Whoever has reach to `/home/lumen/projects/limpet/iso-build/` should either point it
at a real checkout of this repo and drop its own stage list in favor of `./build.sh all`,
or keep it a thin wrapper that still sources the full `STAGES` array rather than
re-enumerating a subset that drifts (as it already has, twice: stage3 and now stage4/8).

## wq#4524 — an ISO with zero RE tools now fails the build

The ticket's three hypotheses, checked against this script:

- **"stage4 failed silently"**: true whenever stage4 ran without a chroot.
  `stage4_package_re_tools` was the only stage that wrapped its installs in
  `if [ -d "$CHROOT" ]` and returned 0 otherwise, so it wrote the manifest, installed
  nothing, and passed. It now fails unless `$CHROOT` is mounted.
- **"CHROOT not inherited"**: `CHROOT` is always `$LIMPET_ISO_WORKDIR/chroot`
  (`build.sh:26`). An exported `CHROOT` is overwritten, so a wrapper that mounts its own
  chroot somewhere else and runs `build.sh <stage>` against a different workdir hits the
  silent skip above.
- **"packing dropped the changes"**: stage11 packs `layers/upper/`, not `$CHROOT`. Writes
  that go to any mount other than stage1b's overlay never reach the squashfs.

The fix doesn't depend on which of these happened. `verify_re_tools` checks every tool in
`tool-packages.json` (apt package installed in the dpkg db, built binary at its
`/usr/local/bin` or `/opt/re-venv/bin` path, artifact dir non-empty). stage4 runs it on
the chroot after installing. stage11 runs it on `upper/` before `mksquashfs`, and fails
if the manifest is missing, i.e. stage4 never ran. The external wrapper's stage subset
(wq#4456) now fails at stage11 instead of shipping a tool-less ISO.

The ticket's figures (104 tools, a 29K `tool-catalog.json`, picocom) don't match this
repo: `limpet/tool-catalog.json` is 73K, lists 44 tools, and has no picocom entry. They
most likely describe the stale out-of-tree copy under `/home/lumen/projects/limpet` that
the wrapper's `LIMPET_ROOT` resolves to (wq#4456). `LIMPET_TOOL_CATALOG` overrides the
catalog path; the tests use it.

## wq#4558 — live layer dpkg database (stage11)

casper stacks `minimal` -> `minimal.standard` -> `minimal.standard.live` with the live layer
on top, and the live layer ships its own full `/var/lib/dpkg/status`. On the stock ISO that
copy shadows the target layer's, so in a live session `dpkg`/`apt` disagree with the files on
disk (packages this build adds look uninstalled, packages it removes look installed; measured
and observed in a booted guest, see `reports/wq4557_live_layer_dpkg_consistency.md`).

`stage11_build_hybrid_iso` therefore also repacks the live layer (`repack_live_layer`): it takes
the original base target-layer status, the original live status and the target layer's status
after this build, three-way merges them (`iso_layers.py merge-dpkg-status`, same for
`apt/extended_states`), and writes the live layer's `.squashfs`, `.manifest`, `.manifest.full`,
`.size` and `md5sum.txt` entries. It always merges from the ORIGINAL base layers, so rebuilds are
idempotent, and fails closed on a conflict. It does nothing when the target layer is itself the
live layer or the base ISO has no live layer. `snap:` records in `manifest.full` (not dpkg
packages) are carried over from the originals for both layers.

Not covered: dpkg diversions, alternatives and trigger state files, which the live layer also
carries (not checked whether this build changes them).

## wq#4523 — installer presence gate (stage11b)

The wq#4523 P0 ("no working installer — ubiquity missing") rested on a false
premise: Xubuntu 26.04 has no ubiquity to be missing. Canonical retired it;
the flavors now install through the `ubuntu-desktop-bootstrap` snap seeded in
the live-only casper layer (`research/WQ3983_XUBUNTU_26_10_DISSECTION.md` §1,
confirmed against the 26.04.1 and 26.10 manifests). "Add ubiquity back" (option
a) is not possible, and the custom TUI wizard (option b) was explicitly
superseded by `docs/deployment-spec.md:5,11,38` ("the installer is the stock
Xubuntu 26.04 installer already on the base ISO, not a custom TUI"). So the fix
is neither: it is a gate that fails the build the moment the stock installer
doesn't survive the remaster.

`stage11b_verify_installer` (`verify_installer.py`) probes the *finished* ISO
— not the build inputs, which wq#4456 showed drift — for: `install-sources.yaml`
present/parseable/`fsimage-layered` with one default source; every referenced
layer squashfs and its parents; the `<default>.live` layer; and, inside it, the
snap seed (`var/lib/snapd/seed/seed.yaml` seeding the installer snap by name
plus its `.snap` file). A missing seed is exactly the ghost "Install" icon
wq#4523 reported. Falsifiable both ways in `test_verify_installer.py` (passes
on the stock layout, fails on seedless / wrong-snap / no-live-layer /
no-install-sources variants). Override the seeded snap name with
`--installer-snap` if a future base ships a different installer.

## wq#4558 — live-only test SSH (replaces wq#4527's `stage3b`)

QEMU automated testing needs SSH, not QMP keystroke injection. A server with known-good auth
must never reach an installed system, so the sshd goes into the **live layer only**
(`minimal.standard.live`, "live-session only, never installed"), never into `minimal.standard`
(the layer `stage3` fills and the installer copies to disk).

It is opt-in and part of the stage 11 live layer repack (`install_live_test_ssh`, called from
`repack_live_layer`), not a separate stage:

- `LIMPET_TEST_SSH_PUBKEY` unset (the default): nothing SSH-related is built, and the path needs
  no extra host commands (no `systemctl`, no mounts).
- `LIMPET_TEST_SSH_PUBKEY=/path/to/key.pub`: after the dpkg database merge above, the build mounts
  an overlay whose lowers are the **modified** target layer and its parents and whose upper is
  the live layer tree, installs `openssh-server` there (apt lists and cache go to the chroot
  tmpfs `/run`, not into the layer, because the base layers carry stale apt lists), writes
  `etc/ssh/sshd_config.d/10-limpet-test.conf` (no passwords, no keyboard-interactive, no root
  login, pubkey only), enables `ssh.service`, and puts the key in `etc/skel/.ssh/authorized_keys`
  (dir `0700`, file `0600`). Because the install runs on top of the merged database, dpkg writes a
  consistent database back into the live layer and the live `manifest.full` lists `openssh-server`.
- Fail closed: a missing or empty key file, a file containing a private key, a target layer that is
  itself the live layer, or a base ISO without a live layer all fail the build instead of silently
  skipping. A failed install unmounts everything it mounted and ships no live layer.
- Build and connect:
  `LIMPET_TEST_SSH_PUBKEY=/home/lumen/.secrets/limpet-test-ssh.pub sudo -E ./build.sh all`, then
  `ssh -p <fwd> -i /home/lumen/.secrets/limpet-test-ssh -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null ubuntu@127.0.0.1`. The live user is `ubuntu`
  (`export USERNAME="ubuntu"` in the base live layer's `/etc/casper.conf`); casper creates it at boot
  from `/etc/skel`, which is how the key reaches it, so the username is never hardcoded here.
- Known limitations: host keys generated by the package postinst are baked into the live layer, so
  every ISO built from it shares them (fine for an ephemeral QEMU test, not for a golden image). The
  build needs network access for `apt-get update`. `LIMPET_ISO_LAYER=*.live` is no longer how test SSH
  is requested (that moved all Limpet content into the live layer).
- Tests: `test_live_test_ssh.py` (fake mount/chroot/apt-get; real shell and merge logic).

## Tests

```sh
python3 -m pytest limpet/iso-builder/ -q
```
