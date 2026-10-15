# wq#3946 — Limpet ISO build pipeline

`build.sh` assembles a Xubuntu 26.04 base + every Limpet component into one
hybrid BIOS+UEFI bootable ISO. Run `./build.sh --help` for the stage list and
env vars, or `./build.sh all` end to end on a build host with real network
egress, `debootstrap`/`xorriso`/`mksquashfs`/`grub-mkrescue`/`qemu-system-x86_64`,
and ~15 GB free (this worktree/CI sandbox has none of those and cannot run it).

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
- **RE tool packaging** (task 4): `tool-catalog.json` records no
  apt/pip/cargo package name field. `gen_tool_packages.py` resolves one the
  way a human would — `dpkg -S <verified-path>` on the build host — and
  reports a tool unresolved rather than guessing a package name. Only
  `core`/`vetted` trust tiers are baked in; `community` stays out until
  promoted (mirrors `limpet/registry/import_catalog.py`'s existing
  skip-by-design behavior).
- **GRUB/Plymouth branding** (task 9): `docs/branding-spec.md` section 6
  explicitly defers the Plymouth boot splash and never specifies a graphical
  GRUB theme. `build.sh stage9` ships a text-mode GRUB menu and leaves
  Plymouth unthemed rather than fabricating either.
- **Hardware profile book is 5 profiles, not 200+** (tasks 5/10): the ticket's
  "200+" is a target the upstream Tier-1/2 tickets haven't reached yet
  (`docs/hardware-tiers-spec.md` so far only has the AMD tier complete).
  `stage5_bundle_hardware_book` globs `limpet/hardware-book/**/*.toml`
  dynamically — it bundles whatever exists and needs no change as more land.

## What this script does NOT do (and why that's not a shortcut)

- **Does not fetch the base ISO.** An unattended multi-GB download with no
  checksum pinned anywhere in this repo is a guess, not a verified input.
  `stage1` requires the caller to supply `LIMPET_BASE_ISO` +
  `LIMPET_BASE_ISO_SHA256` and verifies the hash before extracting.
- **Does not cross-compile DKMS modules.** `build_iso_drivers.py`'s own
  docstring scopes that to "once a pinned target kernel exists" — `stage7`
  installs the udev rules + prebake plan it generates and says exactly what's
  left (wiring `dkms install` against stage1's kernel) rather than faking a
  compile step with no kernel to compile against.
- **Was not run end to end in this session.** The worktree sandbox has none
  of `debootstrap`/`mksquashfs`/`xorriso`/`grub-mkrescue`/`qemu-system-x86_64`
  and no multi-GB scratch space. Every stage that *can* run standalone here
  (`stage4`, `stage7`, `gen_motd.py`, the overlay files, both test suites) was
  run for real — see the done-notes evidence — not merely written.

## Tests

```sh
python3 -m pytest limpet/iso-builder/ -q
```
