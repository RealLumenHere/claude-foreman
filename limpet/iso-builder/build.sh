#!/usr/bin/env bash
# wq#3946 -- Limpet ISO build pipeline: Xubuntu 26.04 base + every Limpet
# component assembled into one installable, hybrid BIOS+UEFI bootable ISO.
#
# Ticket-cited integration point ("install-wizard-spec.md section 6") does not
# exist anywhere in this repo -- confirmed by repo-wide grep, and already
# logged once for the same root cause by wq#3941 (kb/ISSUE-LOG.md row 1327:
# "ISO build script... was never built"). This script is that pipeline; it is
# written from the artifacts that do exist (docs/desktop-spec.md,
# docs/branding-spec.md, limpet/tool-catalog.json, limpet/hardware-book/,
# limpet/hardware.d/, research/wq3921_soc_exploit_seed.json) rather than
# blocked on a spec file that was never written. See iso-builder/README.md for
# the per-stage provenance of every decision this script makes.
#
# Each stage is independently runnable (`build.sh <stage-name>`) so a later
# ticket can resume from wherever a Tier-0/1/2 dependency lands next, and
# fails loudly with the exact missing input/tool rather than fabricating
# output, per the fleet's no-guessing read doctrine.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIMPET_ROOT="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$LIMPET_ROOT/.." && pwd)"

WORKDIR="${LIMPET_ISO_WORKDIR:-$HERE/.build}"
CHROOT="$WORKDIR/chroot"
ISO_EXTRACT="$WORKDIR/iso-extract"
# wq#4077: lower/<layer> mountpoints + the unsquashed target layer (upper/)
# that overlayfs records every chroot change into. See stage1b.
LAYERS_DIR="$WORKDIR/layers"
OUT_DIR="${LIMPET_ISO_OUT_DIR:-$WORKDIR/out}"
ISO_NAME="${LIMPET_ISO_NAME:-limpet-$(date -u +%Y%m%d).iso}"

XUBUNTU_VERSION="26.04"
# Caller-supplied: a locally cached base ISO + its published sha256. This
# script never fetches 2+ GB over the network unattended (instinct 3/5
# territory: an unattended multi-GB fetch with no integrity source pinned in
# this repo is a guess, not a verified input) -- the operator (or an earlier
# pipeline stage with real network egress and the Ubuntu keyring) supplies
# both and this stage only verifies them.
BASE_ISO="${LIMPET_BASE_ISO:-}"
BASE_ISO_SHA256="${LIMPET_BASE_ISO_SHA256:-}"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
fail() { log "FAIL: $*"; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1 (install it on the build host, not in the ISO's chroot)"; }

# ---------------------------------------------------------------------------
# Stage 1 -- base ISO acquisition and extraction
# ---------------------------------------------------------------------------
stage1_fetch_base_iso() {
  log "stage1: base Xubuntu $XUBUNTU_VERSION ISO"
  [ -n "$BASE_ISO" ] || fail "LIMPET_BASE_ISO not set -- point it at a locally cached Xubuntu $XUBUNTU_VERSION ISO (this script does not fetch one itself; see header comment)"
  [ -f "$BASE_ISO" ] || fail "LIMPET_BASE_ISO=$BASE_ISO does not exist"
  # wq#4008: a failed/partial fetch (e.g. four 404s against a release path
  # that isn't published yet) can leave a 0-byte file sitting at this path.
  # Reject that before it ever reaches the checksum check below, with an
  # error that names the actual failure instead of a generic mismatch.
  [ -s "$BASE_ISO" ] || fail "LIMPET_BASE_ISO=$BASE_ISO is 0 bytes -- refusing to build from an empty ISO (a prior fetch likely failed; re-fetch before retrying)"
  [ -n "$BASE_ISO_SHA256" ] || fail "LIMPET_BASE_ISO_SHA256 not set -- supply the published checksum, do not skip verification"
  require_cmd sha256sum
  local actual
  actual="$(sha256sum "$BASE_ISO" | cut -d' ' -f1)"
  [ "$actual" = "$BASE_ISO_SHA256" ] || fail "checksum mismatch on $BASE_ISO: got $actual, expected $BASE_ISO_SHA256"
  require_cmd xorriso
  mkdir -p "$ISO_EXTRACT"
  xorriso -osirrox on -indev "$BASE_ISO" -extract / "$ISO_EXTRACT" >/dev/null
  log "stage1: extracted to $ISO_EXTRACT"
}

# ---------------------------------------------------------------------------
# Stage 1b -- layered chroot assembly (wq#4077)
# ---------------------------------------------------------------------------
# Xubuntu 26.04 has no single filesystem.squashfs: casper stacks
# minimal -> minimal.standard -> minimal.standard.live, and
# casper/install-sources.yaml names the layer the installer copies to disk
# (the default: true source, minimal.standard on the stock ISO). Limpet
# content has to land in THAT layer to survive installation; the .live layer
# is the live session only. This mirrors livecd-rootfs lb_binary_layered: the
# target layer is the overlayfs upperdir over its read-only parents, so every
# later stage's chroot writes (apt, cp, whiteouts for purges) are recorded in
# upper/ and stage11 packs upper/ back into that same layer.
iso_layers() { python3 -I "$HERE/iso_layers.py" "$@"; }

target_layer() {
  if [ -n "${LIMPET_ISO_LAYER:-}" ]; then echo "$LIMPET_ISO_LAYER"; return; fi
  local sources="$ISO_EXTRACT/casper/install-sources.yaml"
  [ -f "$sources" ] || fail "no $sources -- run stage1 first (a base without it is not a layered 26.04+ image and this pipeline does not handle it)"
  iso_layers target-layer --sources "$sources"
}

stage1b_assemble_chroot() {
  log "stage1b: assembling layered chroot"
  require_cmd python3
  require_cmd unsquashfs
  require_cmd mount
  require_cmd mountpoint
  if mountpoint -q "$CHROOT"; then
    log "stage1b: $CHROOT already mounted -- reusing it"
    return
  fi
  local casper="$ISO_EXTRACT/casper" layer lowers
  layer="$(target_layer)"
  [ -f "$casper/$layer.squashfs" ] || fail "target layer $casper/$layer.squashfs missing from the extracted ISO"
  lowers="$(iso_layers lowerdirs --layer "$layer" --root "$LAYERS_DIR/lower")"
  local lower_paths d
  IFS=: read -r -a lower_paths <<<"$lowers"
  for d in "${lower_paths[@]}"; do
    mkdir -p "$d"
    mountpoint -q "$d" || mount -t squashfs -o loop,ro "$casper/$(basename "$d").squashfs" "$d"
  done
  # Unsquash (not loop-mount) the target layer: it is the writable upperdir,
  # and unsquashfs keeps its existing overlay whiteouts + opaque xattrs.
  # An upper/ left by an earlier run holds that run's stage work -- keep it.
  if [ ! -d "$LAYERS_DIR/upper" ]; then
    unsquashfs -d "$LAYERS_DIR/upper" "$casper/$layer.squashfs" >/dev/null
  fi
  mkdir -p "$LAYERS_DIR/work" "$CHROOT"
  mount -t overlay overlay \
    -o "lowerdir=$lowers,upperdir=$LAYERS_DIR/upper,workdir=$LAYERS_DIR/work" "$CHROOT"

  # API filesystems for dpkg maintainer scripts. /run is a tmpfs so the
  # resolver copy below never enters the layer; the chroot's resolv.conf is
  # the stock symlink into /run/systemd/resolve, and the host's stub resolver
  # is reachable from the chroot (same network namespace).
  mount -t proc proc "$CHROOT/proc"
  mount -t sysfs sysfs "$CHROOT/sys"
  mount --bind /dev "$CHROOT/dev"
  mount --bind /dev/pts "$CHROOT/dev/pts"
  mount -t tmpfs tmpfs "$CHROOT/run"
  mkdir -p "$CHROOT/run/systemd/resolve"
  cp -L /etc/resolv.conf "$CHROOT/run/systemd/resolve/stub-resolv.conf"
  # No daemons start from package installs inside the build chroot.
  printf '#!/bin/sh\nexit 101\n' >"$CHROOT/usr/sbin/policy-rc.d"
  chmod +x "$CHROOT/usr/sbin/policy-rc.d"
  log "stage1b: $layer (over $lowers) mounted at $CHROOT"
}

release_chroot() {
  rm -f "$CHROOT/usr/sbin/policy-rc.d"
  local m
  for m in dev/pts dev sys proc run; do
    if mountpoint -q "$CHROOT/$m"; then umount "$CHROOT/$m"; fi
  done
  if mountpoint -q "$CHROOT"; then umount "$CHROOT"; fi
  if [ -d "$LAYERS_DIR/lower" ]; then
    for m in "$LAYERS_DIR"/lower/*; do
      if mountpoint -q "$m"; then umount "$m"; fi
    done
  fi
}

# ---------------------------------------------------------------------------
# Stage 2 -- bloat removal
# ---------------------------------------------------------------------------
stage2_remove_bloat() {
  log "stage2: bloat removal"
  local list="$HERE/bloat-removal.list"
  [ -f "$list" ] || fail "missing $list"
  require_cmd chroot
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  local pkgs
  pkgs="$(grep -v '^#' "$list" | grep -v '^[[:space:]]*$')"
  # wq#4075: one invocation covering the whole list meant a single absent
  # package made apt-get purge the WHOLE list exit non-zero and purge NOTHING,
  # while the old `|| log ... non-fatal` line swallowed that exit code and
  # misreported total failure as a benign partial. Purge package-by-package
  # instead so one absent package only skips itself -- every other package in
  # the list still gets purged, and each outcome is logged for what it is.
  local pkg
  while IFS= read -r pkg; do
    [ -n "$pkg" ] || continue
    if chroot "$CHROOT" apt-get purge -y "$pkg"; then
      log "stage2: purged $pkg"
    else
      log "stage2: $pkg not purged (absent from this base or already removed)"
    fi
  done <<<"$pkgs"
  chroot "$CHROOT" apt-get autoremove -y
}

# ---------------------------------------------------------------------------
# Stage 3 -- Limpet package injection
# ---------------------------------------------------------------------------
stage3_inject_limpet_packages() {
  log "stage3: injecting Limpet components + desktop overlay"
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"

  # Overlay tree (docs/desktop-spec.md panel/launchers, docs/branding-spec.md
  # LightDM greeter config) copies onto the chroot verbatim.
  cp -a "$HERE/overlay/." "$CHROOT/"
  chmod +x "$CHROOT"/opt/limpet/panel/*.sh

  # limpet-tools CLI + registry + HAL daemon + MCP meta-server: the real
  # component code, not a reference to where it lives.
  mkdir -p "$CHROOT/opt/limpet/lib"
  # Production modules only -- an unfiltered *.py glob shipped the pytest suite
  # (17 test_*.py files) into /opt/limpet/lib (wq#4073).
  find "$LIMPET_ROOT" -maxdepth 1 -name '*.py' ! -name 'test_*.py' \
    -exec cp -a {} "$CHROOT/opt/limpet/lib/" \;
  cp -a "$LIMPET_ROOT/registry" "$CHROOT/opt/limpet/lib/"
  cp "$REPO_ROOT/ops/wq3809-limpet-hal/hal.py" "$CHROOT/opt/limpet/lib/hal.py"
  mkdir -p "$CHROOT/opt/limpet/mcp-meta-server"
  cp "$REPO_ROOT/ops/wq3808-limpet-mcp-meta-server-a1/"{meta_server.py,registry.py,stdio_mcp_client.py,cli_wrapper.py} \
    "$CHROOT/opt/limpet/mcp-meta-server/"

  # "installer wizard" in the ticket maps to the Target Device Wizard
  # (docs/target-wizard-spec.md, limpet-wizard.desktop above) -- no OS-level
  # installer UI (ubiquity/calamares equivalent) exists in this repo or is
  # named by any spec; see iso-builder/README.md for this mapping decision.
  mkdir -p "$CHROOT/opt/limpet/docs"
  cp "$REPO_ROOT/docs/target-wizard-spec.md" "$CHROOT/opt/limpet/docs/"

  # precheck service: no implementation or spec exists anywhere in the repo
  # under this name (confirmed by repo-wide grep). Not fabricated here --
  # tracked instead (see README.md "Known gaps").
  log "stage3: precheck service has no source artifact yet -- skipped, see README.md"
}

# ---------------------------------------------------------------------------
# Stage 4 -- RE tool packaging
# ---------------------------------------------------------------------------
stage4_package_re_tools() {
  log "stage4: RE tool packaging from tool-catalog.json"
  require_cmd python3
  local manifest="$WORKDIR/tool-packages.json"
  mkdir -p "$WORKDIR"
  python3 -I "$HERE/gen_tool_packages.py" --catalog "$LIMPET_ROOT/tool-catalog.json" --out "$manifest"
  log "stage4: manifest at $manifest"
  if [ -d "$CHROOT" ]; then
    require_cmd jq
    local pkgs
    pkgs="$(jq -r '.apt_packages[]' "$manifest" | tr '\n' ' ')"
    [ -n "$pkgs" ] && chroot "$CHROOT" apt-get install -y $pkgs
    local unresolved
    unresolved="$(jq '.totals.unresolved' "$manifest")"
    log "stage4: $unresolved catalog tools unresolved on this build host (see $manifest, never guessed)"
    [ "$(jq '.cargo_crates | length' "$manifest")" -gt 0 ] && stage4b_build_cargo_tools "$manifest"
  fi
  return 0
}

# wq#4097: build recipe-backed cargo crates (e.g. binwalk v3) in an ubuntu:$XUBUNTU_VERSION
# container on the build host and copy ONLY the binary into the chroot, so no Rust toolchain
# is added to the ISO layer. Crate/version come from gen_tool_packages.py, which validates
# them and only emits crates that have a verified recipe. Runtime libs were already apt-installed
# by stage4 (they are in manifest.apt_packages).
stage4b_build_cargo_tools() {
  local manifest="$1" out="$WORKDIR/cargo-out" c crate ver bin bdeps
  require_cmd docker; require_cmd jq
  rm -rf "$out"; mkdir -p "$out" "$CHROOT/usr/local/bin"
  while read -r c; do
    crate="$(jq -r .crate <<<"$c")"; ver="$(jq -r .version <<<"$c")"; bin="$(jq -r .bin <<<"$c")"
    bdeps="$(jq -r '.build_apt | join(" ")' <<<"$c")"
    log "stage4b: cargo install $crate $ver (container ubuntu:$XUBUNTU_VERSION, --locked)"
    docker run --rm -v "$out:/out" "ubuntu:$XUBUNTU_VERSION" bash -euc \
      "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq --no-install-recommends cargo rustc build-essential pkg-config ca-certificates $bdeps; cargo install $crate --version $ver --locked --root /out"
    [ -x "$out/bin/$bin" ] || fail "cargo build of $crate $ver produced no $out/bin/$bin"
    install -m 0755 "$out/bin/$bin" "$CHROOT/usr/local/bin/$bin"
    log "stage4b: installed /usr/local/bin/$bin ($(sha256sum "$out/bin/$bin" | cut -c1-16)...)"
  done < <(jq -c '.cargo_crates[]' "$manifest")
}

# ---------------------------------------------------------------------------
# Stage 5 / 10 -- hardware profile book (TOML profiles, target 200+)
# ---------------------------------------------------------------------------
stage5_bundle_hardware_book() {
  log "stage5/10: bundling limpet/hardware-book/"
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  mkdir -p "$CHROOT/opt/limpet/hardware-book"
  cp -a "$LIMPET_ROOT/hardware-book/." "$CHROOT/opt/limpet/hardware-book/"
  local n
  n="$(find "$LIMPET_ROOT/hardware-book" -name '*.toml' | grep -c ^)"
  log "stage5/10: bundled $n profile(s) (ticket target is 200+; this stage bundles whatever exists today and grows automatically as Tier 1/2 tickets land -- no count is hardcoded)"
}

# ---------------------------------------------------------------------------
# Stage 6 -- target device database
# ---------------------------------------------------------------------------
stage6_bundle_target_device_db() {
  log "stage6: bundling limpet/hardware.d/ (RE target/peripheral device catalog)"
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  mkdir -p "$CHROOT/opt/limpet/hardware.d"
  cp "$LIMPET_ROOT"/hardware.d/*.toml "$CHROOT/opt/limpet/hardware.d/"
}

# ---------------------------------------------------------------------------
# Stage 7 -- driver bundle (DKMS + firmware blobs)
# ---------------------------------------------------------------------------
stage7_bundle_driver_bundle() {
  log "stage7: driver bundle via limpet/hardware.d/build_iso_drivers.py"
  require_cmd python3
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  local bundle="$WORKDIR/driver-bundle"
  python3 -I "$LIMPET_ROOT/hardware.d/build_iso_drivers.py" --write-bundle "$bundle"
  # wq#3980: the pinned-kernel hook (driver_hook/) places the udev rules, hardware.d
  # TOMLs, docs and driver-plan.json, and fails closed (exit 3) if the chroot carries a
  # kernel other than driver_hook/kernel.pin, or (exit 4) on an unpinned DKMS source.
  # LIMPET_DRIVER_HOOK_RUN=1 additionally executes the in-chroot installer (needs root +
  # network inside the chroot): firmware apt packages and any pinned DKMS builds.
  local run=()
  [ "${LIMPET_DRIVER_HOOK_RUN:-0}" = "1" ] && run=(--run-chroot)
  python3 -I "$HERE/driver_hook/driver_hook.py" install --chroot "$CHROOT" --bundle "$bundle" "${run[@]}"
  log "stage7: driver bundle installed for the pinned kernel ABI (pin: driver_hook/kernel.pin; see README "Driver bundle hook")"
}

# ---------------------------------------------------------------------------
# Stage 8 -- exploit database snapshot
# ---------------------------------------------------------------------------
stage8_bundle_exploit_db() {
  log "stage8: bundling exploit database snapshot"
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  mkdir -p "$CHROOT/opt/limpet/exploit-db"
  cp "$REPO_ROOT/research/wq3921_soc_exploit_seed.json" \
     "$REPO_ROOT/research/WQ3921_EXPLOIT_VULN_DATABASES.md" \
     "$CHROOT/opt/limpet/exploit-db/"
}

# ---------------------------------------------------------------------------
# Stage 9 -- branding assets (GRUB, Plymouth, LightDM)
# ---------------------------------------------------------------------------
stage9_apply_branding() {
  log "stage9: branding"
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  require_cmd python3

  # Theme/font packages the shipped greeter conf names. wq#3946 shipped
  # overlay/etc/lightdm/lightdm-gtk-greeter.conf pointing at Papirus-Dark /
  # Greybird-dark / Inter without installing any of them, so the greeter booted
  # with icon-theme-name aimed at a theme absent from the chroot (wq#4073).
  # Packages come from docs/branding-spec.md:160-161 and :126, not guessed.
  # Greybird is the Xubuntu default and is normally already present; apt makes
  # that a no-op rather than something this build has to assume.
  chroot "$CHROOT" apt-get install -y \
    papirus-icon-theme greybird-gtk-theme fonts-inter

  # LightDM greeter conf itself: shipped verbatim in overlay/ (stage3).
  # MOTD: rendered from the fixed terminal mark + tokens.json (branding-spec.md 1.3/2.4).
  python3 -I "$HERE/gen_motd.py" --out "$CHROOT/etc/motd"
  mkdir -p "$CHROOT/opt/limpet/brand"
  cp "$REPO_ROOT/design/limpet/brand/limpet-logo.svg" "$REPO_ROOT/design/limpet/brand/tokens.json" \
     "$CHROOT/opt/limpet/brand/"

  local BRAND="$REPO_ROOT/design/limpet/brand"
  # Raster exports (PNGs) rendered from the SVG masters into the chroot's brand
  # dir -- not committed to git (branding-spec.md §5). Needs librsvg2-bin.
  require_cmd rsvg-convert
  bash "$BRAND/export-assets.sh" "$CHROOT/opt/limpet/brand"

  # GRUB graphical theme (wq#3948, design/limpet/brand/themes/grub/theme.txt).
  mkdir -p "$CHROOT/boot/grub/themes/limpet"
  cp "$BRAND/themes/grub/theme.txt" "$CHROOT/boot/grub/themes/limpet/"
  cp "$CHROOT/opt/limpet/brand/grub/limpet-logo-128.png" \
     "$CHROOT/boot/grub/themes/limpet/"
  python3 -I "$HERE/set_grub_theme.py" \
    --default "$CHROOT/etc/default/grub" \
    --theme "/boot/grub/themes/limpet/theme.txt"

  # Plymouth boot splash (wq#3948, design/limpet/brand/themes/plymouth/).
  local PLY="$CHROOT/usr/share/plymouth/themes/limpet"
  mkdir -p "$PLY"
  cp "$BRAND/themes/plymouth/limpet.plymouth" "$BRAND/themes/plymouth/limpet.script" "$PLY/"
  cp "$CHROOT/opt/limpet/brand/plymouth/limpet-logo-256.png" \
     "$CHROOT/opt/limpet/brand/plymouth/ray-dot.png" "$PLY/"

  # LightDM greeter CSS + XFCE accent sheet (wq#3948).
  mkdir -p "$CHROOT/usr/share/lightdm" "$CHROOT/etc/skel/.config/gtk-3.0"
  cp "$BRAND/themes/lightdm/limpet-greeter.css" "$CHROOT/usr/share/lightdm/"
  cp "$BRAND/themes/gtk/gtk.css" "$CHROOT/etc/skel/.config/gtk-3.0/gtk.css"

  # Desktop wallpaper: export-assets.sh already rendered limpet-wallpaper.png
  # into the brand dir; the xfconf key that points xfdesktop at it is set by
  # the desktop overlay (docs/desktop-spec.md §4.2), not here.

  log "stage9: applied GRUB theme, Plymouth splash, LightDM/GTK themes, icons, wallpaper (wq#3948)"
}

# ---------------------------------------------------------------------------
# Stage 11 -- hybrid BIOS+UEFI bootable ISO
# ---------------------------------------------------------------------------
# wq#4077: the old body packed the whole chroot as one filesystem.squashfs and
# ran grub-mkrescue on a tree with no boot/grub/grub.cfg. Instead: pack the
# target layer's upper/ back into its own casper/<layer>.squashfs, refresh the
# metadata livecd-rootfs derives from it, and write a new ISO from the base ISO
# with only the changed files mapped in and its El Torito/MBR/GPT boot records
# replayed (xorriso -boot_image any replay; proven by
# scratch/limpet-iso/test_remaster.sh, 99 s for the live layer). Kernel and
# initrd stay the base ISO's /casper/vmlinuz + /casper/initrd.
stage11_build_hybrid_iso() {
  log "stage11: assembling hybrid BIOS+UEFI ISO"
  require_cmd python3
  require_cmd mksquashfs
  require_cmd unsquashfs
  require_cmd xorriso
  require_cmd dpkg-query
  [ -n "$BASE_ISO" ] && [ -f "$BASE_ISO" ] || fail "LIMPET_BASE_ISO must still point at the base ISO -- stage11 replays its boot records"
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  local casper="$ISO_EXTRACT/casper" upper="$LAYERS_DIR/upper" layer parent
  layer="$(target_layer)"
  [ -d "$upper" ] || fail "no $upper -- run stage1b_assemble_chroot first"
  parent="$(iso_layers lowerdirs --layer "$layer" --root . | cut -d: -f1)"
  parent="${parent#./}"

  local changes="$WORKDIR/iso-changes"
  rm -rf "$changes"
  mkdir -p "$changes/casper" "$changes/boot/grub" "$OUT_DIR"

  # Measured on the merged tree before it is unmounted, the way
  # lb_binary_layered does: manifest.full = every package installed up to this
  # layer, the delta manifest = what this layer adds over its parent, and the
  # install-sources size = the installed footprint the installer checks.
  if mountpoint -q "$CHROOT"; then chroot "$CHROOT" apt-get clean; fi
  dpkg-query --show --admindir="$CHROOT/var/lib/dpkg" >"$changes/casper/$layer.manifest.full"
  diff -NU0 "$casper/$parent.manifest.full" "$changes/casper/$layer.manifest.full" \
    | grep -v '^@' >"$changes/casper/$layer.manifest" || true
  local merged_size
  merged_size="$(du -B 1 -s -x "$CHROOT/" | cut -f1)"
  release_chroot
  du -B 1 -s "$upper" | cut -f1 >"$changes/casper/$layer.size"

  # Same compression + block size as the layer being replaced.
  local comp bs
  comp="$(unsquashfs -s "$casper/$layer.squashfs" | awk '/^Compression/{print $2}')"
  bs="$(unsquashfs -s "$casper/$layer.squashfs" | awk '/^Block size/{print $3}')"
  mksquashfs "$upper" "$changes/casper/$layer.squashfs" -noappend -comp "$comp" -b "$bs" -xattrs -no-progress >/dev/null

  # install, not cp: xorriso extracts ISO files 0444 and these get rewritten.
  install -m 0644 "$casper/install-sources.yaml" "$changes/casper/"
  iso_layers set-size --sources "$changes/casper/install-sources.yaml" --layer "$layer" --size "$merged_size"

  # The ISO's own boot menu (distinct from the installed system's grub, which
  # stage9 themes). Rebranded from the stock entries, kernel args untouched.
  local cfg
  for cfg in grub.cfg loopback.cfg; do
    install -m 0644 "$ISO_EXTRACT/boot/grub/$cfg" "$changes/boot/grub/"
    iso_layers grub-cfg --file "$changes/boot/grub/$cfg"
  done

  local rel=()
  mapfile -t rel < <(cd "$changes" && find . -type f | sed 's|^\./||' | sort)
  install -m 0644 "$ISO_EXTRACT/md5sum.txt" "$changes/md5sum.txt"
  iso_layers md5sum --root "$changes" "${rel[@]}"

  local map_args=() f
  for f in "${rel[@]}" md5sum.txt; do map_args+=(-map "$changes/$f" "/$f"); done
  rm -f "$OUT_DIR/$ISO_NAME"
  xorriso -indev "$BASE_ISO" -outdev "$OUT_DIR/$ISO_NAME" -boot_image any replay \
    "${map_args[@]}" -commit >/dev/null 2>"$WORKDIR/xorriso-stage11.log" \
    || fail "xorriso repack failed -- see $WORKDIR/xorriso-stage11.log"
  log "stage11: mapped ${#rel[@]} changed files + md5sum.txt (incl. /casper/$layer.squashfs) onto a copy of $BASE_ISO"
  local size_gb
  size_gb="$(du -BG "$OUT_DIR/$ISO_NAME" | cut -f1 | tr -d G)"
  log "stage11: wrote $OUT_DIR/$ISO_NAME (${size_gb}G; ticket target is 8-12G)"
}

# ---------------------------------------------------------------------------
# Stage 12 -- QEMU/KVM boot test
# ---------------------------------------------------------------------------
stage12_qemu_test() {
  log "stage12: QEMU boot smoke test"
  require_cmd qemu-system-x86_64
  local iso="$OUT_DIR/$ISO_NAME"
  [ -f "$iso" ] || fail "no ISO at $iso -- run stage11 first"
  local log_file="$WORKDIR/qemu-boot.log"
  timeout 180 qemu-system-x86_64 -m 4096 -cdrom "$iso" -boot d \
    -nographic -serial "file:$log_file" -display none \
    -no-reboot || true
  grep -qi 'login:' "$log_file" || fail "QEMU boot did not reach a login prompt within 180s -- see $log_file"
  log "stage12: boot reached login prompt"
}

# ---------------------------------------------------------------------------
STAGES=(
  stage1_fetch_base_iso
  stage1b_assemble_chroot
  stage2_remove_bloat
  stage3_inject_limpet_packages
  stage4_package_re_tools
  stage5_bundle_hardware_book
  stage6_bundle_target_device_db
  stage7_bundle_driver_bundle
  stage8_bundle_exploit_db
  stage9_apply_branding
  stage11_build_hybrid_iso
  stage12_qemu_test
)

usage() {
  cat <<EOF
Usage: $0 [stage-name|all]

Stages (run in this order for 'all'):
$(printf '  %s\n' "${STAGES[@]}")

Env:
  LIMPET_BASE_ISO         path to a cached Xubuntu $XUBUNTU_VERSION ISO (stage1)
  LIMPET_BASE_ISO_SHA256  its published sha256 (stage1)
  LIMPET_ISO_LAYER        casper layer to modify (default: the install-sources.yaml
                          default source, minimal.standard on stock 26.04)
  LIMPET_ISO_WORKDIR      scratch dir (default: $HERE/.build)
  LIMPET_ISO_OUT_DIR      output dir (default: \$WORKDIR/out)
  LIMPET_ISO_NAME         output filename (default: limpet-<date>.iso)
EOF
}

main() {
  local target="${1:-}"
  mkdir -p "$WORKDIR"
  if [ -z "$target" ] || [ "$target" = "-h" ] || [ "$target" = "--help" ]; then
    usage; exit 0
  fi
  if [ "$target" = "all" ]; then
    for s in "${STAGES[@]}"; do "$s"; done
    return
  fi
  for s in "${STAGES[@]}"; do
    if [ "$s" = "$target" ]; then "$s"; return; fi
  done
  fail "unknown stage: $target (see --help)"
}

main "$@"
