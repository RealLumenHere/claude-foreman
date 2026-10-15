# wq#3983 — Xubuntu 26.10 base ISO anatomy for Limpet OS foundation

**Method:** downloaded and dissected the real artifact, not secondary docs. Live web
search was attempted first per doctrine (`fleet-search web "Ubuntu 26.10
ubuntu-desktop-bootstrap flutter installer"` and a second query) and both failed —
all four general engines suspended/CAPTCHA'd, no Brave fallback key. This is the
already-logged outage at `kb/ISSUE-LOG.md` rows 715/823 (wq#2595/2599/2601,
missing `BRAVE_API_KEY`), not a new issue, so it is not re-logged. Primary-artifact
dissection is strictly better evidence for this ticket than secondary web sources
would have been, so the gap did not block the research.

Artifact: `xubuntu-26.10-snapshot4-desktop-amd64.iso` (26.10 is not yet final;
`cdimage.ubuntu.com/xubuntu/releases/26.10/release/` currently only publishes daily
snapshots — this ticket dissects **snapshot-4**, built 2026-09-17, the newest
available at run time 2026-10-15). SHA256 verified against the snapshot's own
`SHA256SUMS`: `a43ee0b7…92577` — exact match. Codename confirmed from
`.disk/info`: **"Stonking Stingray"**.

Downloaded to `/tmp/fworker-wq3983/` (scratch, not part of this branch): ISO (5,363,128,320
bytes), the three `casper/*.squashfs` layers extracted via `7z` (no mount/sudo
needed), `unsquashfs -l` full listings of each layer, and targeted `unsquashfs`
extractions of config paths. The complete real package list — `dpkg status` pulled
from the live layer's squashfs (1,908 `Package:` stanzas) and the ISO's own
`.manifest` (1,920 lines incl. snaps) — is committed alongside this doc:
`research/wq3983_xubuntu-26.10-snapshot4-desktop-amd64.manifest` and
`research/wq3983_xubuntu-26.04.1-desktop-amd64.manifest` (delta baseline).

---

## 1. ISO structure, boot chain, installer

Top-level layout (`7z l`, 802 files / 136 folders): `.disk/`, `EFI/`, `boot/`,
`boot.catalog`, `casper/`, `dists/`, `md5sum.txt`, `pool/`. **No `isolinux/` at
all** — this image is GRUB-only for both firmware paths, not legacy-syslinux +
GRUB:
- BIOS: `boot/grub/i386-pc/*.mod` + `boot/grub/grub.cfg`, booted via the El Torito
  `boot.catalog`.
- UEFI: `EFI/boot/bootx64.efi` (signed shim), `grubx64.efi` (GRUB2), `mmx64.efi`
  (MokManager) — full Secure Boot chain.

`boot/grub/grub.cfg` (extracted in full) is a 20-line menu: "Try or Install
Xubuntu" (`quiet splash`), "Xubuntu (safe graphics)" (`nomodeset`), and on EFI
only "UEFI Firmware Settings" (`fwsetup`). No GRUB theme, no background image, no
`gfxmode` beyond `gfxpayload=keep` — plain text menu. **Answers task 4's GRUB-theme
question directly: there is no GRUB theme to replace, Limpet can add one from
scratch with no upstream conflict.**

**`casper/` uses the layered-squashfs scheme, not a single `filesystem.squashfs`**
(this is *not* new in 26.10 — confirmed unchanged from 26.04.1 by diffing
`xubuntu-26.04.1-desktop-amd64.list`, which has the identical `minimal` /
`minimal.standard` / `minimal.standard.live` triple):

| layer | compressed | installed (own `.size`) | role |
|---|---|---|---|
| `minimal.squashfs` | 1.82 GB | 3.92 GiB | base rootfs |
| `minimal.standard.squashfs` | 2.09 GB | 7.24 GiB *(cumulative, see §10)* | + full desktop |
| `minimal.standard.live.squashfs` | 185 MB | 10.03 GiB *(cumulative)* | + live-session-only bits |

`casper/install-sources.yaml` (extracted, 27 lines) is the installer's own
manifest of these layers — `type: fsimage-layered`, two selectable sources
(`xubuntu-desktop-minimal` → `minimal.squashfs` only, `xubuntu-desktop` →
`minimal.standard.squashfs`, `default: true`). The *live-session* adds a third,
non-selectable live-only layer on top for both.

**Installer: neither Ubiquity nor Calamares.** Grepped the full 1,908-package
`dpkg status` and the 1,920-line `.manifest` for both names — zero hits, in both
26.10 and the 26.04.1 baseline. The live image instead snap-preseeds
`ubuntu-desktop-bootstrap` (`snap:ubuntu-desktop-bootstrap 26.10/stable/ubuntu-26.10
675` in the manifest) — Canonical's Flutter-based installer that replaced Ubiquity
fleet-wide starting with 23.10/24.04 GNOME flavors and has now reached Xubuntu.
It reads `casper/install-sources.yaml` to offer the minimal/full choice shown
above.

**Can Limpet replace it with a TUI wizard?** Yes, cleanly — `ubuntu-desktop-bootstrap`
is a *snap*, not baked into the squashfs rootfs; dropping it from the seed and
substituting Limpet's own installer (the Target Device Wizard,
`docs/target-wizard-spec.md`, already the ISO-builder's stated integration point
per `limpet/iso-builder/README.md`) requires no squashfs surgery, just omitting
that snap from the preseed list and shipping the wizard's own `casper/*-hooks`
equivalent. The `install-sources.yaml` mechanism itself (pick a `fsimage-layered`
source) is worth keeping as-is since Limpet's own minimal/full split can reuse it
verbatim.

## 2. Package manifest

Full list: `research/wq3983_xubuntu-26.10-snapshot4-desktop-amd64.manifest`
(1,920 lines) — this *is* the complete dpkg+snap inventory, cross-checked against
a real `unsquashfs`-extracted `var/lib/dpkg/status` (1,908 `dpkg` stanzas; the
12-line gap is snap entries counted in the manifest but not dpkg).

Focus-area versions (all pulled from the extracted `status`/`.manifest`, not
guessed):

| component | version | note |
|---|---|---|
| XFCE (panel/session/thunar/power-mgr/notifyd/xfconf/xfdesktop/xfwm4) | **4.20.x** | see §3 |
| LightDM | 1.32.0-6ubuntu4 | + `lightdm-gtk-greeter` 2.0.9-0ubuntu3.1 |
| NetworkManager | 1.58.1-1ubuntu3 | |
| PulseAudio vs PipeWire | **PipeWire 1.6.8-1ubuntu3** is the stack (`pipewire-pulse` is the compat shim; no `pulseaudio` package in manifest at all) | |
| BlueZ | 5.87-3 | + `bolt` 0.9.11-1 (Thunderbolt) |
| kernel | **7.2.0-5-generic** (`linux-generic` 7.2.0-5.5) | §6 |
| Python | **3.14.7-3** (`python3.14` 3.14.7-4ubuntu1) | no `python3-pip`, no `pip` anywhere in manifest |
| snap status | `snapd` 2.77.1 present and active | §9 |

Limpet keep/strip/replace/add categorization (by subsystem, not line-by-line —
1,908 packages is not individually triageable in one ticket; this sets the rule
each future packaging ticket, wq#3947, applies):

- **Keep as-is:** kernel + `linux-firmware-*` (hardware coverage is the entire
  point of riding a real Ubuntu kernel), `bluez`+`bolt`, `xserver-xorg-video-*`
  (GPU breadth), `mesa-utils`, NetworkManager stack, XFCE core (`xfwm4`,
  `xfce4-session`, `xfconf`, `thunar`, `xfdesktop4`), LightDM core.
- **Strip (bloat-removal target, see §10):** `libreoffice*` not present in this
  manifest (Xubuntu dropped it from the default seed already — verified absent),
  but `thunderbird` (snap), `onboard`+`onboard-data` if Limpet ships its own a11y
  story, every `language-pack-*`/`language-support-*` beyond `en`, printer stack
  (`cups*`, present per `multi-user.target.wants/cups.service`) if Limpet targets
  headless/field hardware without printers.
- **Replace:** `ubuntu-desktop-bootstrap` snap → Limpet Target Device Wizard
  (§1); `lightdm-gtk-greeter` → Limpet branded greeter (blocks wq#3948, LightDM
  greeter API notes in §4); Plymouth `xubuntu-logo`/`xubuntu-text` themes →
  Limpet boot theme (also §4).
- **Add:** `git`, `cmake`, `python3-pip` — grepped for all three in the live
  manifest, **zero hits**, confirmed absent from the default desktop seed (§8).
  `dkms` — also absent from the manifest (§6/§14) — needed if Limpet pre-stages
  any out-of-tree RE hardware driver.

## 3. XFCE specifics

Exact versions from `status`/`.manifest`: `xfce4-panel` 4.20.8-1, `xfce4-session`
4.20.4-1, `xfce4-settings` 4.20.1-1ubuntu2, `xfconf` 4.20.0-2, `xfdesktop4`
4.20.1-1ubuntu2, `xfwm4` 4.20.0-1build1, `xfce4-power-manager` 4.20.1-1,
`xfce4-notifyd` 0.9.7-2build1, `thunar` 4.20.9-1. Whisker Menu is not in the
manifest under that name — Xubuntu 26.10 ships the stock XFCE "Applications
Menu" (`xfce4-appmenu-plugin`/panel default), not `xfce4-whiskermenu-plugin`;
grepped the manifest for `whisker`, zero hits, so a Limpet panel that wants
Whisker-style search-as-you-type must add `xfce4-whiskermenu-plugin` itself, not
assume it is already there.

Panel plugin API / build system: XFCE panel plugins at this version build against
`libxfce4panel-2.0` + `xfconf` via the standard Meson/`xfce4-dev-tools` pipeline
(confirmed by `xfce4-panel` 4.20's API major being `-2.0`, the post-GTK3 panel
ABI). This is unchanged from 26.04 — no XFCE major-version bump to account for
(§16).

## 4. Display stack

**X11, not Wayland, is the default session.** The manifest carries only Wayland
*client* libraries (`libwayland-client0`/`-cursor0`/`-egl1`/`-server0` 1.26.0-1,
pulled in as dependencies of other packages) — no `xwayland`, no
`wayland-protocols`, no `*-session-wayland` package, no GDM/wayland-session
plumbing. `xserver-xorg-core` 2:21.1.22-1ubuntu4 is present and is what actually
runs. Confirmed independently at the systemd level: `display-manager.service`
(extracted symlink) → `/lib/systemd/system/lightdm.service` — LightDM is not just
installed, it is the active target.

LightDM greeter API for a custom greeter: `lightdm-gtk-greeter` 2.0.9 is the
shipped greeter; its config (`etc/lightdm/lightdm-gtk-greeter.conf`,
`etc/lightdm/lightdm.conf.d/{01_debian,02_ubuntu}.conf`) is the integration
surface — a Limpet greeter either themes `lightdm-gtk-greeter.conf` (fastest
path) or ships a new `.desktop` file under `/usr/share/xgreeters/` and points
`[Seat:*] greeter-session=` at it in `lightdm.conf.d/` (confirmed this is the
real mechanism by reading the extracted, non-empty `lightdm.conf`: the
`greeters-directory` default is
`$XDG_DATA_DIRS/lightdm/greeters:$XDG_DATA_DIRS/xgreeters`).

Plymouth: default theme resolved via the real `/etc/alternatives/default.plymouth`
symlink (extracted) → `/usr/share/plymouth/themes/xubuntu-logo/xubuntu-logo.plymouth`.
Eight themes ship total (`default`, `details`, `text`, `text.plymouth`, `tribar`,
`ubuntu-text`, `xubuntu-logo`, `xubuntu-text`) — Limpet adds a ninth rather than
fighting the alternatives system, then repoints the alternative.

GRUB theme: none shipped (§1) — clean slate for branding (wq#3948).

## 5. Systemd services, boot breakdown

Real enabled-unit lists extracted from `etc/systemd/system/{multi-user,graphical}.target.wants/`
(not guessed): `multi-user.target.wants` includes `NetworkManager.service`,
`ModemManager.service`, `anacron.service`, `apport.service`, `avahi-daemon.service`,
`chrony.service`, `console-setup.service`, `cron.service`, `cups.service`+`cups-browsed.service`+`cups.path`,
`dmesg.service`, `e2scrub_reap.service`, `grub-initrd-fallback.service`,
`grub2-common.service`, `networkd-dispatcher.service`, `remote-fs.target`,
`rsyslog.service`, `secureboot-db.service`, `snapd.apparmor.service` (+ more,
full list in the squashfs extraction notes above). `graphical.target.wants`
includes `udisks2.service`, `accounts-daemon.service` (+ more).

Disable-for-Limpet candidates (present, generally unneeded on a dedicated RE
appliance): `cups*` (no printer story on most target hardware), `ModemManager`
(no modem hardware on the stated target devices), `apport` (crash-reporter
telemetry, actively unwanted on a security tool), `ubuntu-report`-adjacent
telemetry if present once the `standard`/`live` layers are unsquashed in a
follow-up (not reached in this pass — budget went to the areas above).
Full per-unit boot-time breakdown (`systemd-analyze blame`) needs a booted
instance; this sandbox has no `systemd-analyze`/boot capability, so that
specific number is **not** fabricated here — flagged as an open item for
whichever ticket first boots the Limpet ISO in QEMU (wq#3946 already notes this
sandbox can't run `qemu-system-x86_64` either).

## 6. Kernel

`linux-image-7.2.0-5-generic` / `linux-generic` **7.2.0-5.5**, confirmed two
ways: the `dpkg status` entry and the real `usr/lib/modules/7.2.0-5-generic/`
directory name inside `minimal.squashfs` (8,225 files under that tree — `.ko`
modules plus `modules.*` index files, extracted listing, not estimated).
`dkms` itself is **absent** from the manifest — confirmed by grep, zero hits —
so Limpet must add it for any out-of-tree module (common for RE-fleet USB/SDR
hardware).

## 7. Network

NetworkManager 1.58.1 is the active network stack (`multi-user.target.wants/NetworkManager.service`
enabled). `etc/NetworkManager/NetworkManager.conf` (extracted, full content):
```
[main]
plugins=ifupdown,keyfile

[ifupdown]
managed=false

[device]
wifi.scan-rand-mac-address=no
```
Notable: **MAC randomization is explicitly disabled** (`wifi.scan-rand-mac-address=no`)
in the stock image — a deliberate stance Limpet should decide on consciously for
a fleet of RE/pentest laptops (randomization is usually *wanted* on an attack
box; shipping the Ubuntu default silently works against that). BlueZ 5.87-3,
`bolt` 0.9.11 (Thunderbolt). USB WiFi driver coverage rides the kernel's own
module tree (§6) plus `linux-firmware-{broadcom,intel,marvell,mediatek,qualcomm,realtek}-wireless`
packages, all present in the manifest — breadth is a function of the stock
kernel/firmware package set, not anything Xubuntu adds on top.

## 8. Dev tools

Checked the full manifest for all four: `gcc` present (`gcc-15` 15.3.0-3ubuntu1,
meta `gcc` 4:15.2.0-5ubuntu1), `make` present (4.4.1-3), **`git` absent**,
**`cmake` absent**, **`pip`/`python3-pip` absent** (all three: zero grep hits).
`python3` itself is present (3.14.7-3). Limpet must add `git`, `cmake`,
`python3-pip` explicitly — they are not transitively pulled in by anything in
the default desktop seed.

## 9. Snap status

`snapd` 2.77.1+ubuntu26.10.1 is installed and the manifest shows a full
preseeded snap set: `core24`, `firefox` (`stable/ubuntu-26.10`, confirms Firefox
ships as a **snap**, not a deb — the `firefox` deb entry in the manifest,
`1:1snap1-0ubuntu10`, is the transitional stub that pulls the snap), `gnome-46-2404`,
`gtk-common-themes`, `mesa-2404`, `snapd-desktop-integration`, `snap-store`,
`thunderbird`, `ubuntu-desktop-bootstrap` (the installer, §1), `firmware-updater`.
Clean removal path: disable/mask `snapd.service`+`snapd.socket`, drop all
`snap:*` manifest lines from the seed, replace Firefox with the `.deb`/apt
channel (Mozilla's own apt repo, or omit Firefox entirely if Limpet doesn't ship
a browser by default) — no deb in the base image depends on snapd itself
(grepped `status` for `Depends:.*snapd`, no hits outside snapd's own metapackages),
so removal is seed-level, not a dependency fight.

## 10. Disk footprint

Real installer-stated sizes from `casper/install-sources.yaml` (the installer's
own size metadata, not computed): **minimal install 3.92 GiB** (`minimal.squashfs`
alone), **default/full install 7.24 GiB** (`minimal.squashfs` + `minimal.standard.squashfs`
combined, source `id: xubuntu-desktop`, `default: true`), **full live session
~10.03 GiB** (+ `minimal.standard.live.squashfs`'s own 2.1 GiB delta, live-only
extras not present in an installed system). Per-layer compressed-on-ISO sizes:
1.82 GB / 2.09 GB / 185 MB respectively.

2–4 GB bloat-removal target against the 7.24 GiB full-install baseline is
realistic: `thunderbird` snap (~200MB+), `cups*` stack, `onboard`+`-data`,
non-`en` `language-pack-*`/`language-support-*` (this is typically the single
largest strippable chunk on a desktop seed, though this pass didn't unsquash
`standard.squashfs` to size it precisely — flagged as a follow-up for wq#3947),
GNOME-snap dependencies pulled in only for the (replaceable) installer
(`gnome-46-2404`, `mesa-2404`, `gtk-common-themes` — all exist solely to support
`ubuntu-desktop-bootstrap`/Firefox snaps, both already marked Replace in §2).

## 11. ISO remastering approach

Tool survey against what this image actually is (not generic Cubic/live-build
advice): the ISO is GRUB-only hybrid BIOS+UEFI with Secure Boot shim (§1) and a
3-layer `fsimage-layered` casper scheme (§1/§10) driven by `install-sources.yaml`.
- **Cubic**: convenient interactively (mounts the ISO, chroots into
  `filesystem.squashfs`), but Cubic's whole model assumes a *single* squashfs —
  it does not natively understand the 3-layer `minimal`/`standard`/`live` split
  this image actually uses, so a Cubic-based pipeline would have to either
  flatten the layers (losing the installer's own minimal/full choice) or be
  patched. Not recommended as the primary path.
  `live-build`**: Debian/Ubuntu's own tool, is layer-aware (it's literally what
  produces this layering upstream) and already evidenced in-repo:
  `limpet/iso-builder/build.sh` (currently targeting 26.04 — **needs its base-release
  bump to 26.10 per this ticket's "BASE OS CHANGED" directive before wq#3946 ships**)
  already assumes a `debootstrap`/`xorriso`/`mksquashfs`/`grub-mkrescue` toolchain,
  i.e. a live-build-shaped pipeline, not Cubic. This research confirms that
  choice was already correct independent of this ticket.
- **mkisofs/xorriso by hand**: viable for the final repack step (this ticket
  used bare `7z`/`unsquashfs` successfully for *read*, proving the format is
  ordinary GRUB+squashfs with no proprietary wrapping), but hand-rolling the
  3-layer squashfs assembly from scratch duplicates what live-build already does
  — only worth it if Limpet drops the minimal/full split entirely.

**Recommendation: keep the existing live-build-shaped `build.sh` pipeline,
re-target it at 26.10 (version string + archive mirror paths), and preserve the
3-layer casper scheme** since `install-sources.yaml` is reusable almost verbatim
for Limpet's own minimal/full product split.

## 12. Upgrade path, LTS vs non-LTS

26.10 is non-LTS: **9 months of upstream support** (standard Ubuntu interim
release) vs 26.04's 5-year LTS window — confirmed against the actual release
directory structure (`cdimage.ubuntu.com/xubuntu/releases/` lists `26.04` with a
`26.04.1` point release already cut, the LTS point-release pattern; `26.10` has
only snapshot builds, the interim-release pattern, no point releases possible
within a 9-month cycle). Implication for Limpet: **basing on 26.10 means Limpet
itself inherits a 9-month upstream security window**, not 5 years — either
Limpet's own release cadence must re-base onto a new Ubuntu interim release
roughly every 6 months to stay inside supported upstream packages, or Limpet
needs its own backport/security-patch commitment once 26.10 goes EOL
independent of upstream. This is a material cadence decision this ticket
surfaces but does not make — it's Frank's call per the instinct-3 autonomy rule
only insofar as it's a product-strategy choice, not a blocking gate (full
autonomy doctrine: record it, don't park on it). Recommendation to carry
forward to the ISO-builder ticket: pin Limpet's base explicitly by point-in-time
snapshot (as this research did, snapshot-4) rather than floating on `26.10`,
since a non-LTS archive mutates fast and a floating base breaks reproducibility
long before the 9-month EOL does.

## 13. AppArmor

Present and **enforcing by default** — confirmed by extracting
`etc/apparmor.d/` and finding zero entries under `etc/apparmor.d/disable/` or
`etc/apparmor.d/force-complain/` (370 profile-related files under `minimal`, 10
more under `standard`, none disabled/complain-forced), and by `grub.cfg`'s
kernel cmdline carrying no `apparmor=0`. Version is a notable 26.10-vs-26.04
jump: **`apparmor` 6.0.0~alpha1-0ubuntu1** here vs **5.0.2-0ubuntu1~26.04.1** on
26.04.1 (§16) — Xubuntu 26.10 ships an alpha-versioned AppArmor as its stable
default, which is a real risk flag for RE-tool interference (ptrace restrictions,
unprivileged-user-namespace confinement, profile transitions on tools like gdb/
Frida that this fleet uses) that a pre-26.10 fleet member would not have hit.
Recommendation: keep AppArmor (it's load-bearing for several other packages'
postinst scripts and for snapd if snapd is kept at all), but budget explicit
verification time in wq#3946/wq#3947 for whether the alpha AppArmor breaks any
of the fleet's RE tool invocations before shipping — don't assume parity with
the 26.04 behavior this fleet has already tuned around.

## 14. Hardware

Kernel 7.2.0-5-generic is the single rolling kernel for 26.10 (interim releases
don't ship a separate HWE track — HWE only exists as a concept on LTS, where
26.04 gets periodic kernel point-bumps under the same ABI; 26.10's kernel *is*
already the "latest hardware" kernel by virtue of being non-LTS). USB3/Thunderbolt:
`bolt` 0.9.11 present (Thunderbolt daemon), kernel module tree confirmed present
at `usr/lib/modules/7.2.0-5-generic/` (8,225 files, §6) which is where
USB3/xHCI/Thunderbolt drivers live — specific module names weren't enumerated
individually in this pass (would require unsquashfs'ing the full module tree,
budgeted against §10's bloat-sizing instead). GPU: Mesa **26.2.2-1ubuntu1**
(`libgl1-mesa-dri`), full `xserver-xorg-video-{amdgpu,ati,intel,nouveau,radeon,vesa,fbdev,qxl}`
driver set present — both Intel and AMD covered out of the box, plus the
`linux-firmware-{intel,amd,nvidia}-graphics` firmware packages.

## 15. Accessibility

Present: `onboard`+`onboard-common`+`onboard-data` 1.4.4.3+ds-1ubuntu1 (on-screen
keyboard), `at-spi2-core`+`at-spi2-common` 2.61.90-1 (the accessibility bus XFCE
apps hook into). **Absent**: no `orca` (screen reader) anywhere in the manifest
— Xubuntu's flavor seed has never carried it (GNOME-only historically), so this
is not a 26.10 change, just a fact to note. Recommendation: strip `onboard` if
Limpet targets keyboard-equipped RE hardware exclusively (most of the fleet's
target devices per `limpet/hardware.d/` are), keep `at-spi2-core` since several
XFCE components soft-depend on the bus being present even when unused.

## 16. Delta from 26.04 (26.04.1 → 26.10 snapshot-4, same package set, real diffs)

| package | 26.04.1 | 26.10 | significance |
|---|---|---|---|
| kernel (`linux-generic`) | 7.0.0-30.30 | **7.2.0-5.5** | kernel minor-version bump |
| apparmor | 5.0.2-0ubuntu1~26.04.1 | **6.0.0~alpha1-0ubuntu1** | major jump, alpha — see §13 |
| network-manager | 1.54.3-2ubuntu3 | **1.58.1-1ubuntu3** | minor bump |
| bluez | 5.85-4ubuntu0.1 | **5.87-3** | minor bump |
| pipewire | 1.6.2-1ubuntu1.1 | **1.6.8-1ubuntu3** | minor bump |
| snapd | 2.76.3+ubuntu26.04 | **2.77.1+ubuntu26.10.1** | minor bump |
| python3 | 3.14.3-0ubuntu2 | **3.14.7-3** | point bump only |
| thunar | 4.20.7-1 | **4.20.9-1** | point bump |
| xfce4-panel | 4.20.7-1 | **4.20.8-1** | point bump |
| xfce4-session | 4.20.4-1 | 4.20.4-1 | **unchanged** |
| lightdm | 1.32.0-6ubuntu4 | 1.32.0-6ubuntu4 | **unchanged** |
| gcc | 4:15.2.0-5ubuntu1 | 4:15.2.0-5ubuntu1 | **unchanged** |
| plymouth | …-0ubuntu8 | …-0ubuntu10 | packaging-only, same upstream snapshot |

**XFCE is not getting a major-version bump** — both releases ship the 4.20
series; every XFCE package delta is a point release. The casper layering scheme
(§1) is also unchanged. The only deltas that actually matter for Limpet's
porting effort are the kernel bump (new module tree path, re-verify any
out-of-tree driver notes in `limpet/hardware.d/`) and the AppArmor major/alpha
jump (§13, re-verify RE tool interference). Everything else is a version-string
update with no behavioral surface change worth re-deriving Limpet's existing
26.04-era decisions over.

---

## Open items (not reached in this pass, named rather than guessed)

- `standard.squashfs`/`standard.live.squashfs` were listed (`unsquashfs -l`) but
  not individually unsquashed for config inspection the way `minimal.squashfs`
  was — language-pack sizing (§10) and the full enabled-service set beyond
  `minimal`'s own `*.wants` dirs live there. Next research/build pass should
  budget for it.
- `systemd-analyze blame`-style boot-time breakdown needs a booted instance;
  this sandbox cannot boot the ISO (same constraint `limpet/iso-builder/README.md`
  already documents for its own build step).
- Individual kernel module names (not just the 8,225-file count) weren't
  enumerated — needed before wq#3941's driver-catalog work can cite exact
  module coverage against `limpet/hardware.d/*.toml` target devices.

These are handed to wq#3946 (ISO builder — needs the 26.10 re-target called out
in §11), wq#3947 (deb packaging — needs the keep/strip/replace/add rules in §2),
and wq#3948 (branding — needs the greeter/Plymouth/GRUB-theme surface in §4).
