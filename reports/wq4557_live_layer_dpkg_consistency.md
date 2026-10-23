# wq#4557: dpkg database consistency across casper layers

Date: 2026-10-22 PDT. Author: limpet-open-0818f6. Scope: research only, no ISO rebuilt, no layer modified (read-only `unsquashfs -cat/-l`, `xorriso -extract`, one QEMU boot of an existing ISO).

Tags: **[V]** measured here, **[U]** upstream behaviour not re-derived.

## 1. Summary

The Limpet build modifies only the target layer `minimal.standard`. The live layer `minimal.standard.live` is shipped unchanged and carries its own full copy of `/var/lib/dpkg/status`. In a booted live session that copy is on top of the overlay, so `dpkg` and `apt` see a package set that does not match the files:

- packages the build **added** look **not installed** (`tmux`: `un`, file present),
- packages the build **removed** look **installed** (`cups`: `ii`, file gone).

This affects every ISO built so far, independent of SSH. It also fixes the shape of the live-only SSH design (wq#4558): the live layer must be rebuilt with a merged package database, not just have sshd dropped into it.

Recommendation: a three-way stanza merge of `var/lib/dpkg/status` performed before the live layer is repacked (section 6).

## 2. Mechanism [V]

- The initrd ships `conf/conf.d/default-layer.conf` with `LAYERFS_PATH=minimal.standard.live.squashfs`. No `layerfs-path=` appears on the grub kernel lines, so this default is what runs.
- `scripts/casper` derives the chain from the dotted file name (`minimal.standard.live` -> `minimal.standard` -> `minimal`), mounts each layer, and for each one **prepends** it to the overlay list (`rofslist="${croot}${imagename} ${rofslist}"`). Result: `lowerdir=live:standard:minimal`. The live layer has the highest priority.
- The initrd in `limpet-20261021.iso` is byte-identical to the base ISO initrd.
- The live layer in `limpet-20261021.iso` is byte-identical to the base live layer (unmodified).

## 3. Measurements on limpet-20261021.iso [V]

| Item | Value |
|------|-------|
| Package stanzas, base parent `minimal.standard` | 1816 |
| Package stanzas, live layer | 1903 (1902 `install ok installed`, 1 `deinstall ok config-files`: `dracut`) |
| Live-only packages (in live, not in parent) | 88 |
| Parent-only (in parent, not in live status) | 1 (`dracut-core`) |
| Stanzas that differ parent vs live at the same version | 126, metadata only (`Priority`, `Section`, `Status`, `Config-Version`) |
| Packages the build **added** to the parent | 11: devilspie2, fonts-inter, legacy-printer-app, liblua5.1-0, libpappl-retrofit1, libpappl1t64, papirus-icon-theme, python3-pyudev, socat, tmux, xfce4-genmon-plugin |
| ...of those, visible in live-layer status | **0 of 11** |
| Packages the build **removed** from the parent | 91 (cups, cups-browsed, gnome-mines, ...) |
| ...of those, still `installed` in live-layer status | **91 of 91** |
| Packages whose version the build changed | 2: jq, libjq1 (1.8.1-4ubuntu1 -> 1.8.1-4ubuntu2); live status still says -4ubuntu1 |
| Live-only packages that Depend/Pre-Depend on a removed package | 0 (so dropping the removed stanzas is dependency-safe today) |

Other dpkg/apt state in the live layer that can shadow the parent's copy: `var/lib/dpkg/diversions` and `diversions-old`, `var/lib/dpkg/alternatives/{awk,idmap-plugin,shimx64.efi.signed}`, `etc/alternatives` (7 entries), `var/lib/dpkg/triggers/{shim-kernel-check,shim-secureboot-policy,update-initramfs}`, `var/lib/apt/extended_states`, `var/lib/systemd/deb-systemd-helper-enabled`, plus 342 `var/lib/dpkg/info/*` entries (per-package, no conflict with parent files).

## 4. Observed in a booted guest [V]

`limpet-20261021.iso` booted in QEMU (docs/qemu-agent-reference.md template, KVM). 1280x800 from ~60 s, desktop at ~190 s. Ctrl+Alt+T opened a terminal; the command below was typed through QMP and the guest wrote its output to `/dev/ttyS0` (host `-serial file:`):

    (dpkg -l tmux cups; ls /usr/bin/tmux /usr/sbin/cupsd) 2>&1 | sudo tee /dev/ttyS0

Output (guest side):

    ii  cups  2.4.16-1ubuntu1 amd64 Common UNIX Printing System(tm) - PPD/driver support, web interface
    un  tmux  <none>          <none> (no description available)
    ls: cannot access '/usr/sbin/cupsd': No such file or directory
    /usr/bin/tmux

So `dpkg` reports cups installed with no cupsd binary, and tmux not installed while `/usr/bin/tmux` exists. Raw output: `/home/lumen/scratch/research/limpet-4557/bootcheck2.out`.

## 5. Options

| Option | Result | Verdict |
|--------|--------|---------|
| (a) Rebuild the live layer chroot as overlay `[minimal, modified standard] + original live` and run apt/dpkg | The original live upper (stale status) is still the topmost copy of the file, so dpkg copies it up and the result is still stale | Insufficient alone; needs (b) anyway |
| **(b) Three-way merge of `status` (base parent, live, modified parent), write result into the live layer, repack it** | Correct `dpkg -l`/`apt` in the live session; touches only metadata we control | **Recommended** |
| (c) Delete the live layer `status` so the parent shows through | The 88 live-only packages (casper etc.) vanish from the database, the 126 live metadata tweaks and the `dracut` config-files state are lost; their `info/` files remain without a status entry | Rejected |
| (d) Regenerate the live layer from scratch with Canonical's livecd-rootfs chain | Correct by construction, but needs their whole build environment | Out of scope |
| (e) Repeat every Limpet package operation inside the live layer too | Doubles work, still needs the removals mirrored | Rejected |

## 6. Recommended design (b)

Pure function `merge_dpkg_status(base, live, modified)` in `iso_layers.py`, keyed by (Package, Architecture):

1. In `modified` only (added by the build): take the modified stanza.
2. In `base` and `modified`, `modified == base` (untouched by the build): take the **live** stanza; if live lacks it (e.g. `dracut-core`), drop it.
3. In `base` and `modified`, `modified != base` (touched, e.g. jq): take the modified stanza. If live also differs from base in a non-metadata field (`Version`, `Depends`, `Pre-Depends`, `Conflicts`, `Provides`), **fail closed**.
4. In `base`, not in `modified` (removed by the build): drop, even if live lists it. First assert that no kept stanza Depends/Pre-Depends on it, else fail closed.
5. In `live` only (88 packages): take the live stanza.

Expected result on this ISO: 1903 - 91 + 11 = **1823 stanzas (measured on the merged result: 1819 `install ok installed` + 4 `deinstall ok config-files`)**; jq/libjq1 at -4ubuntu2.

Inputs: base parent status via `unsquashfs -cat` of the **original** `casper/<target>.squashfs` (before repack), modified parent status from the chroot, live status from the **original** `minimal.standard.live.squashfs`. Always merge from the originals so rebuilds are idempotent.

Stage 11 changes (build.sh ~586-650): after the target layer is packed, unsquash the original live layer to `$LAYERS_DIR/live-upper`, write the merged `var/lib/dpkg/status` (and the `extended_states` union, see below), `mksquashfs` with the original's compression and block size (`unsquashfs -s`, `-xattrs`, `-noappend`), refresh `minimal.standard.live.manifest`, `.manifest.full` and `.size`, add the files to `$changes`, extend `md5sum.txt`, and keep `install-sources.yaml` pointing at the target layer only. wq#4558 reuses the same step for its live-only sshd.

Companion state files: `apt/extended_states` - union with the build's `apt-mark auto` additions; `diversions`, `alternatives`, `triggers`, `deb-systemd-helper-enabled` - assert the build did not change the parent's copies (compare against base), fail closed if it did.

## 7. Test plan

Unit (fake data, wq#2630 style): merge cases for each of rules 1-5 plus both fail-closed branches; idempotence (merging twice from the originals gives the same bytes); mutation checks (drop rule 4, drop rule 2) must fail a test.

Real data (read-only, on `.build`): run the merge on the real three status files; assert 11 added present, 91 removed absent, 88 live-only present, jq = -4ubuntu2, total = 1823.

Booted guest (same QEMU recipe as section 4): `dpkg -l tmux` is `ii`; `dpkg -l cups` is not installed (`dpkg-query: no packages found`); `dpkg --audit` prints nothing; installed count 1819 (`install ok installed`).

## 8. Unknowns

- Not run: the merge itself, a repacked live layer, or a boot of one (research only, no rebuild).
- `dpkg --audit` cleanliness after the merge is predicted, not observed.
- Behaviour of `apt` upgrade/removal in the live session on the current (inconsistent) database was not exercised.

## 9. Reproduction

Scripts and raw output: `/home/lumen/scratch/research/limpet-4557/` (`bootcheck.sh`, `qtype.py`, `bootcheck2.out`). Layer inspection used `unsquashfs -cat <layer>.squashfs var/lib/dpkg/status` and `xorriso -osirrox on -indev <iso> -extract /casper/<file>` on `/home/lumen/foreman/limpet/iso-builder/.build/iso-extract/casper` and `limpet-20261021.iso`; casper logic from `unmkinitramfs .../casper/initrd` -> `scripts/casper` lines ~596-683 and `conf/conf.d/default-layer.conf`.

## 10. Corrections

- 2026-10-23: the first version of this report predicted 1822 installed + 1 `rc`. Running the merge on the real files (wq#4558) gave 1823 stanzas of which 1819 are `install ok installed` and 4 are `deinstall ok config-files` (config residue from the build's package removals). Sections 6 and 7 are corrected above.
