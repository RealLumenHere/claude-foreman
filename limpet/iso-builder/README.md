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
  `loopback.cfg` ("Try or Install Limpet", kernel args unchanged) and
  refreshes `md5sum.txt`. Finally it writes a copy of the base ISO with only
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
- **Does not cross-compile DKMS modules.** `build_iso_drivers.py`'s own
  docstring scopes that to "once a pinned target kernel exists" — `stage7`
  installs the udev rules + prebake plan it generates and says exactly what's
  left (wiring `dkms install` against stage1b's kernel) rather than faking a
  compile step with no kernel to compile against.
- **Has not been run end to end as root against the real ISO.**
  `test_layered_remaster.py` runs the real stage1 -> 1b -> 6 -> 11 path
  (real mksquashfs/unsquashfs/xorriso/dpkg-query) on a small synthetic
  three-layer ISO built from the real 26.04 `install-sources.yaml`/`grub.cfg`
  (`fixtures/xubuntu-26.04/`). Only mount/umount/mountpoint/chroot are faked,
  because they need root. Boot-record replay on the real ISO is covered by
  scratch/limpet-iso/test_remaster.sh (wq#4077 evidence).

## Tests

```sh
python3 -m pytest limpet/iso-builder/ -q
```
