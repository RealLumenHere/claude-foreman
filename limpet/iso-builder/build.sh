#!/usr/bin/env bash
# wq#3946 -- Limpet ISO build pipeline: Xubuntu base (26.10; LIMPET_XUBUNTU_VERSION, wq#4709) + every Limpet
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

# wq#4713: resolve the symlink chain before deriving HERE. The retired
# out-of-tree wrapper invoked this script through a symlink farm, so a logical
# dirname made LIMPET_ROOT the foreign wrapper tree and stage4 read its stale,
# untiered tool-catalog.json (0 of 104 RE tools baked). readlink -f pins HERE to
# the real repo whatever path the script is invoked by.
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
LIMPET_ROOT="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$LIMPET_ROOT/.." && pwd)"

# wq#4771: canonical, so findmnt's (canonical) TARGETs compare against it and the
# teardown's "only ever under WORKDIR" guard can't be sidestepped by a relative or
# symlinked override.
WORKDIR="$(realpath -m -- "${LIMPET_ISO_WORKDIR:-$HERE/.build}")"
case "$WORKDIR" in
  / | /dev | /dev/* | /proc | /proc/* | /sys | /sys/* | /run | /run/*)
    printf 'FAIL: LIMPET_ISO_WORKDIR=%s is a host system path -- the build mounts and unmounts under it (wq#4771)\n' "$WORKDIR" >&2; exit 1 ;;
esac
CHROOT="$WORKDIR/chroot"
ISO_EXTRACT="$WORKDIR/iso-extract"
# wq#4077: lower/<layer> mountpoints + the unsquashed target layer (upper/)
# that overlayfs records every chroot change into. See stage1b.
LAYERS_DIR="$WORKDIR/layers"
OUT_DIR="${LIMPET_ISO_OUT_DIR:-$WORKDIR/out}"
ISO_NAME="${LIMPET_ISO_NAME:-limpet-$(date -u +%Y%m%d).iso}"
TOOL_CATALOG="${LIMPET_TOOL_CATALOG:-$LIMPET_ROOT/tool-catalog.json}"
# stage4 writes it, stage11 refuses to pack a layer that does not carry what it lists.
TOOL_MANIFEST="$WORKDIR/tool-packages.json"

# wq#4709: the base release is a parameter. Frank set Xubuntu 26.10 as the Limpet
# base (lumen_state limpet.base_os, 2026-10-15); 26.04 stays buildable. Each
# release has its own driver_hook/kernel-<release>.pin (ISO name + sha256 + kernel
# ABI), and the same value tags the ubuntu:<release> build containers (stage4b/4c).
XUBUNTU_VERSION="${LIMPET_XUBUNTU_VERSION:-26.10}"
KERNEL_PIN="$HERE/driver_hook/kernel-$XUBUNTU_VERSION.pin"
[ -f "$KERNEL_PIN" ] || { printf 'FAIL: no %s -- LIMPET_XUBUNTU_VERSION=%s is not a pinned release\n' "$KERNEL_PIN" "$XUBUNTU_VERSION" >&2; exit 1; }
pin_value() { sed -n "s/^$1=//p" "$KERNEL_PIN"; }
# Caller-supplied: a locally cached base ISO. This script never fetches 2+ GB
# over the network unattended (instinct 3/5 territory) -- the operator (or an
# earlier pipeline stage with real network egress and the Ubuntu keyring)
# supplies it and this stage only verifies it. Its sha256 defaults to the
# release pin's, which was checked against the published SHA256SUMS; stage1 warns
# on a caller override that differs, since the pinned kernel ABI is only verified
# for that exact image.
BASE_ISO="${LIMPET_BASE_ISO:-}"
BASE_ISO_SHA256="${LIMPET_BASE_ISO_SHA256:-$(pin_value BASE_ISO_SHA256)}"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
fail() { log "FAIL: $*"; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1 (install it on the build host, not in the ISO's chroot)"; }

# ---------------------------------------------------------------------------
# Mount teardown (wq#4710) -- stage1b mounts an overlay chroot plus proc/
# sysfs/devtmpfs/tmpfs inside WORKDIR, and nothing used to unmount them on a
# failed/killed run: a watchdog SIGTERM or a dead fworker left a build's
# mounts stuck under WORKDIR with no process left to tear them down.
# devtmpfs mounted inside the tree means an `rm -rf WORKDIR` from a worktree
# prune or pruner timer then descends into the host's real device nodes.
#
# workdir_mounts lists mounts under WORKDIR deepest-path-first (so a child
# unmounts before its parent). findmnt ships in the same util-linux package
# as mount/mountpoint, both already required by stage1b.
# ---------------------------------------------------------------------------
workdir_mounts() {
  findmnt -rn -o TARGET 2>/dev/null \
    | awk -v d="$WORKDIR" -v dslash="$WORKDIR/" \
      '$0 == d || index($0, dslash) == 1 { print length($0), $0 }' \
    | sort -rn | cut -d' ' -f2-
}

_unmount_retry() {
  local m="$1" tries=0
  # wq#4771: teardown only ever unmounts strictly below WORKDIR. A path outside it is
  # a host mount (the 2026-10-25 teardown left Lumen with no /dev/pts for 3 days), so
  # refuse loudly instead of umount -l'ing it five times.
  case "$m" in
    "$WORKDIR"/?*) ;;
    *) log "teardown: refusing to unmount $m -- not under $WORKDIR (wq#4771)"; return 0 ;;
  esac
  while mountpoint -q "$m" 2>/dev/null && [ "$tries" -lt 5 ]; do
    umount "$m" 2>/dev/null || umount -l "$m" 2>/dev/null || true
    tries=$((tries + 1))
  done
  mountpoint -q "$m" 2>/dev/null && log "teardown: $m still mounted after $tries attempt(s) -- something still holds it open (check fuser/lsof)"
  return 0
}

teardown_workdir_mounts() {
  local m
  # Known shape first, in dependency-safe order: the chroot's own submounts, then the
  # chroot overlay itself (so nothing still references layers/lower/* as a lowerdir),
  # then each lower/<layer> squashfs. Unmounting a lowerdir while its overlay is still
  # mounted on top of it is the one ordering a generic deepest-path sort gets wrong --
  # lower/<layer> and chroot are siblings, not nested, so string/path depth alone
  # doesn't know the overlay depends on it.
  for m in "$CHROOT/dev/pts" "$CHROOT/dev" "$CHROOT/sys" "$CHROOT/proc" "$CHROOT/run" "$CHROOT"; do
    _unmount_retry "$m"
  done
  if [ -d "$LAYERS_DIR/lower" ]; then
    for m in "$LAYERS_DIR"/lower/*; do
      [ -e "$m" ] || continue
      _unmount_retry "$m"
    done
  fi
  # Catch-all (wq#4710): anything still mounted under WORKDIR the list above doesn't
  # know about -- a future stage1b change, or a mount left by a differently-shaped
  # failure. Deepest path first so a child clears before its parent.
  for m in $(workdir_mounts); do
    _unmount_retry "$m"
  done
}

# Refuses when a mount sits at or below $1 -- the belt-and-braces half of the
# class fix: whatever removes WORKDIR (this script, a pruner timer, a
# worktree prune) must call this first so a stale mount under the tree turns
# into a loud failure instead of `rm -rf` silently descending into it.
refuse_rm_if_mounted() {
  local target="$1" hit
  hit="$(findmnt -rn -o TARGET 2>/dev/null \
    | awk -v d="$target" -v dslash="$target/" '$0 == d || index($0, dslash) == 1')"
  [ -z "$hit" ] || fail "refusing to remove $target: mount(s) still present at/below it ($(printf '%s' "$hit" | tr '\n' ' ')) -- run '$0 teardown' first (wq#4710: rm -rf here would descend into live devtmpfs/proc/overlay)"
}

stage_teardown() {
  require_cmd mountpoint
  require_cmd umount
  require_cmd findmnt
  log "teardown: unmounting everything under $WORKDIR"
  teardown_workdir_mounts
  refuse_rm_if_mounted "$WORKDIR"
  log "teardown: $WORKDIR is clean"
}

# EXIT fires on both `fail`'s exit 1 and a `set -e` abort -- tear down only on
# a non-zero exit so a clean single-stage run (e.g. `build.sh
# stage1b_assemble_chroot`) keeps its mounts for the next invocation, exactly
# like the existing "already mounted -- reusing it" resume path expects.
# INT/TERM (watchdog kill, a dead fworker's process group) never reach a
# clean exit on their own, so they tear down unconditionally, then exit 143
# instead of returning control to whatever was interrupted.
_teardown_on_exit() {
  local rc=$?
  trap - EXIT INT TERM
  if [ "$rc" -ne 0 ]; then
    log "build.sh exiting (rc=$rc) -- tearing down mounts under $WORKDIR so a failed run never leaves them stuck (wq#4710)"
    teardown_workdir_mounts || true
  fi
  exit "$rc"
}
_teardown_on_signal() {
  trap - EXIT INT TERM
  log "build.sh received SIG$1 -- tearing down mounts under $WORKDIR (wq#4710)"
  teardown_workdir_mounts || true
  exit 143
}
trap _teardown_on_exit EXIT
trap '_teardown_on_signal INT' INT
trap '_teardown_on_signal TERM' TERM

# wq#4456: these two guards previously existed only in the external build-limpet.sh
# wrapper (wq#4449), so running this script's own stage1b directly -- which the README
# says `./build.sh all` supports -- bypassed both entirely. Inlined here so the canonical
# pipeline fails closed on its own, with no wrapper required.
#
# A non-root unsquashfs extracts the target layer with every file owned by the invoking
# user, so sudo(-rs) at /usr/bin/sudo is no longer uid 0 with the setuid bit and refuses to
# run ("must be owned by uid 0 and have the setuid bit set") once installed on the live ISO.
# stage1b keeps an existing upper/ across runs, so one bad non-root extraction silently
# poisons every later build until someone notices on a booted ISO.
verify_layer_ownership() {
  local root="$1" rc=0 n d b r t u m
  for d in usr/bin usr/sbin etc/sudoers.d; do
    [ -d "$root/$d" ] || continue
    n="$(find "$root/$d" -xdev -type f ! -uid 0 2>/dev/null | wc -l)"
    if [ "$n" -gt 0 ]; then
      log "verify-layer: $n regular file(s) under /$d not owned by uid 0"
      rc=1
    fi
  done
  for b in sudo su pkexec passwd newgrp chsh chfn gpasswd mount umount fusermount3; do
    r="$root/usr/bin/$b"
    [ -L "$r" ] || [ -f "$r" ] || continue
    if [ -L "$r" ]; then
      t="$(readlink -f "$r")"
      [ -n "$t" ] && [ -f "$t" ] || continue
      r="$t"
    fi
    read -r u m < <(stat -c '%u %a' "$r")
    if [ "$u" != "0" ]; then
      log "verify-layer: $r (/usr/bin/$b) owned by uid $u (want 0)"
      rc=1
    fi
    case "$m" in
      4[0-7][0-7][0-7]) : ;;
      *) log "verify-layer: $r (/usr/bin/$b) mode $m lacks setuid"; rc=1 ;;
    esac
  done
  return "$rc"
}

# wq#4596: verify_layer_ownership only catches a non-root extraction; it says nothing
# about whether a kept upper/ was unsquashed from the squashfs this run is actually
# targeting. A upper/ left over from a different base ISO or a different target layer
# (e.g. after LIMPET_ISO_LAYER changes, or a base ISO respin) would otherwise be reused
# silently, baking its contents into whatever layer this run packs. The fingerprint pins
# upper/ to the exact (squashfs sha256, layer name) pair it was unsquashed from.
layer_fingerprint() {
  printf '%s %s\n' "$(sha256sum "$1" | cut -d' ' -f1)" "$2"
}

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
  [ "$BASE_ISO_SHA256" = "$(pin_value BASE_ISO_SHA256)" ] ||
    log "stage1: WARNING LIMPET_BASE_ISO_SHA256 is not the $XUBUNTU_VERSION base pinned in $KERNEL_PIN -- its KERNEL_ABI is unverified for this image"
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

# wq#4771: the chroot's /dev is its own tmpfs with the handful of nodes a package
# install needs, plus its own devpts instance -- never a bind of the host's /dev or
# /dev/pts. The bind made $CHROOT/dev a peer of the host /dev under systemd's shared
# propagation, so the teardown's umount of $CHROOT/dev/pts reached the host /dev/pts
# (Lumen ran 3 days with no PTYs), and anything that chowned through $CHROOT/dev
# changed the host's real nodes (/dev/ptmx found lumen:lumen). Same node set as
# debootstrap's setup_devices_simple. $1 is the chroot root, already mounted private.
populate_chroot_dev() {
  local dev="$1/dev"
  mount -t tmpfs -o mode=0755,nosuid tmpfs "$dev"
  mknod -m 666 "$dev/null" c 1 3
  mknod -m 666 "$dev/zero" c 1 5
  mknod -m 666 "$dev/full" c 1 7
  mknod -m 666 "$dev/random" c 1 8
  mknod -m 666 "$dev/urandom" c 1 9
  mknod -m 666 "$dev/tty" c 5 0
  mkdir -p "$dev/pts" "$dev/shm"
  chmod 1777 "$dev/shm"
  mount -t devpts -o newinstance,ptmxmode=0666,mode=0620,gid=5,nosuid,noexec devpts "$dev/pts"
  ln -sfn pts/ptmx "$dev/ptmx"
  ln -sfn /proc/self/fd "$dev/fd"
  ln -sfn /proc/self/fd/0 "$dev/stdin"
  ln -sfn /proc/self/fd/1 "$dev/stdout"
  ln -sfn /proc/self/fd/2 "$dev/stderr"
}

stage1b_assemble_chroot() {
  log "stage1b: assembling layered chroot"
  require_cmd python3
  require_cmd unsquashfs
  require_cmd mount
  require_cmd mountpoint
  require_cmd findmnt
  require_cmd mknod
  # wq#4456: mount/unsquashfs/the proc+sys+dev bind-mounts below all require root; let
  # a missing sudo fail here with a clear message instead of a confusing mid-stage
  # "Operation not permitted" from whichever mount call happens to run first.
  [ "$(id -u)" -eq 0 ] || fail "stage1b must run as root (sudo -E $0 ...) -- mount/unsquashfs need it"
  if mountpoint -q "$CHROOT"; then
    log "stage1b: $CHROOT already mounted -- reusing it"
    return
  fi
  # wq#4710: $CHROOT itself isn't mounted (the reuse path above didn't fire), but a
  # submount survives under WORKDIR anyway -- a prior run was killed/failed between
  # mounting proc/sys/dev/run and the final overlay mount, or after release_chroot
  # unmounted some but not all of them. Reusing silently here would mount a fresh
  # overlay over/under whatever is left; refuse and point at the new teardown stage.
  local stale
  stale="$(workdir_mounts)"
  [ -z "$stale" ] || fail "stage1b: $WORKDIR already has mount(s) left from a previous run ($(printf '%s' "$stale" | tr '\n' ' ')) -- run '$0 teardown' first (wq#4710)"
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
  # An upper/ left by an earlier run holds that run's stage work -- keep it, but only
  # after confirming it wasn't left by a non-root extraction (wq#4456/wq#4449).
  if [ -d "$LAYERS_DIR/upper" ]; then
    verify_layer_ownership "$LAYERS_DIR/upper" ||
      fail "existing $LAYERS_DIR/upper has wrong ownership/setuid (stale non-root extraction) -- move it aside and rerun: mv '$LAYERS_DIR/upper' '$LAYERS_DIR/upper.stale-$(date +%Y%m%d_%H%M%S)'"
    # wq#4596: a kept upper/ must match the squashfs+layer this run is assembling, or
    # a run started against a different base ISO/layer silently reuses another run's
    # upper/ -- e.g. the Oct 18 upper found still holding openssh-server on 2026-10-23.
    local want got_fp
    want="$(layer_fingerprint "$casper/$layer.squashfs" "$layer")"
    got_fp="$(cat "$LAYERS_DIR/upper.fingerprint" 2>/dev/null || true)"
    [ "$got_fp" = "$want" ] ||
      fail "existing $LAYERS_DIR/upper has no/stale fingerprint for $casper/$layer.squashfs (wanted '$want', found '$got_fp') -- move it aside and rerun: mv '$LAYERS_DIR/upper' '$LAYERS_DIR/upper.stale-$(date +%Y%m%d_%H%M%S)'"
  else
    unsquashfs -d "$LAYERS_DIR/upper" "$casper/$layer.squashfs" >/dev/null
    layer_fingerprint "$casper/$layer.squashfs" "$layer" >"$LAYERS_DIR/upper.fingerprint"
  fi
  mkdir -p "$LAYERS_DIR/work" "$CHROOT"
  mount -t overlay overlay \
    -o "lowerdir=$lowers,upperdir=$LAYERS_DIR/upper,workdir=$LAYERS_DIR/work" "$CHROOT"
  # wq#4771: private before anything is mounted beneath it, so every submount below is
  # private too and no mount or unmount under the chroot propagates to any other mount.
  mount --make-private "$CHROOT"

  # API filesystems for dpkg maintainer scripts. /run is a tmpfs so the
  # resolver copy below never enters the layer; the chroot's resolv.conf is
  # the stock symlink into /run/systemd/resolve, and the host's stub resolver
  # is reachable from the chroot (same network namespace).
  mount -t proc proc "$CHROOT/proc"
  mount -t sysfs sysfs "$CHROOT/sys"
  populate_chroot_dev "$CHROOT"
  mount -t tmpfs tmpfs "$CHROOT/run"
  mkdir -p "$CHROOT/run/systemd/resolve"
  cp -L /etc/resolv.conf "$CHROOT/run/systemd/resolve/stub-resolv.conf"
  # No daemons start from package installs inside the build chroot.
  printf '#!/bin/sh\nexit 101\n' >"$CHROOT/usr/sbin/policy-rc.d"
  chmod +x "$CHROOT/usr/sbin/policy-rc.d"
  log "stage1b: $layer (over $lowers) mounted at $CHROOT"
}

# wq#4710: generalized into teardown_workdir_mounts (findmnt-driven, not a hardcoded
# dev/pts,dev,sys,proc,run list) so it also catches whatever a future stage1b change
# mounts, and so the same logic backs the standalone `teardown` stage and the
# EXIT/INT/TERM trap above.
release_chroot() {
  rm -f "$CHROOT/usr/sbin/policy-rc.d" 2>/dev/null || true
  teardown_workdir_mounts
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
  chmod +x "$CHROOT"/opt/limpet/panel/*.sh 2>/dev/null || true

  # wq#4525: overlay/ and the chroot drifted apart silently once before (desktop
  # entries + genmon scripts shipped under different names than this stage
  # injected). Fail loudly here instead of shipping an ISO with a half-copied
  # overlay.
  while IFS= read -r -d '' src; do
    rel="${src#"$HERE"/overlay/}"
    [ -e "$CHROOT/$rel" ] || fail "stage3: overlay file $rel did not land in chroot (cp -a above silently dropped it)"
  done < <(find "$HERE/overlay" -type f -print0)

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
# Stage 3c -- base network utilities (wq#4547)
# ---------------------------------------------------------------------------
# The stock Xubuntu 26.04 casper stack ships wget and iproute2 but NOT curl or
# net-tools (ifconfig/netstat): checked against the minimal, minimal.standard
# and minimal.standard.live manifest.full files of the base ISO. Anyone
# debugging a booted ISO expects them. Installed into the same layer stage3
# fills (the one the installer copies to disk), so they survive installation.
# apt-get install is idempotent, so re-running the stage is safe.
stage3c_install_base_utils() {
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  log "stage3c: installing curl and net-tools (absent from every base layer)"
  chroot "$CHROOT" apt-get install -y curl net-tools
  log "stage3c: curl and net-tools installed"
}

# ---------------------------------------------------------------------------
# Stage 4 -- RE tool packaging
# ---------------------------------------------------------------------------
stage4_package_re_tools() {
  log "stage4: RE tool packaging from $TOOL_CATALOG"
  require_cmd python3
  require_cmd jq
  require_cmd chroot
  # wq#4524: this used to be `if [ -d "$CHROOT" ]; then ...install...; fi; return 0`, the
  # only stage that skipped instead of failing on a missing chroot -- so a run whose
  # CHROOT didn't match the one stage1b mounted wrote the manifest, installed nothing, and
  # exited 0, and the ISO shipped with zero RE tools.
  mountpoint -q "$CHROOT" || fail "chroot not mounted at $CHROOT (run stage1b_assemble_chroot with the same LIMPET_ISO_WORKDIR)"
  mkdir -p "$WORKDIR"
  python3 -I "$HERE/gen_tool_packages.py" --catalog "$TOOL_CATALOG" --out "$TOOL_MANIFEST"
  log "stage4: manifest at $TOOL_MANIFEST"
  local pkgs
  pkgs="$(jq -r '.apt_packages[]' "$TOOL_MANIFEST" | tr '\n' ' ')"
  if [ -n "$pkgs" ]; then chroot "$CHROOT" apt-get install -y $pkgs; fi
  log "stage4: $(jq '.totals.unresolved' "$TOOL_MANIFEST") catalog tools unresolved on this build host (see $TOOL_MANIFEST, never guessed)"
  if [ "$(jq '.cargo_crates | length' "$TOOL_MANIFEST")" -gt 0 ]; then stage4b_build_cargo_tools "$TOOL_MANIFEST"; fi
  if [ "$(jq '.pip_packages + .pip_git_packages | length' "$TOOL_MANIFEST")" -gt 0 ]; then stage4c_build_pip_tools "$TOOL_MANIFEST"; fi
  if [ "$(jq '.binary_artifacts | length' "$TOOL_MANIFEST")" -gt 0 ]; then stage4d_fetch_binary_tools "$TOOL_MANIFEST"; fi
  if [ "$(jq '.source_build_tools | length' "$TOOL_MANIFEST")" -gt 0 ]; then stage4e_build_source_tools "$TOOL_MANIFEST"; fi
  verify_re_tools "$CHROOT" || fail "stage4: installs reported success but the tools above are missing from $CHROOT, or a core-tier tool was unresolved (see $TOOL_MANIFEST .unresolved)"
  log "stage4: every manifest tool verified present in $CHROOT"
}

# Is <path> an executable inside <root>? Resolves symlinks AS IF <root> were /: a pipx app
# link is absolute (/usr/local/bin/unblob -> /usr/local/share/pipx/venvs/unblob/bin/unblob),
# and `[ -x "$root$path" ]` lets the kernel resolve that target against the HOST root, so a
# correctly baked tool read as missing (and a host-only target read as present).
root_exec_ok() {
  local root="$1" p="$2" n=0 t
  while [ -L "$root$p" ] && [ "$n" -lt 16 ]; do
    t="$(readlink "$root$p")"
    case "$t" in /*) p="$t" ;; *) p="$(dirname "$p")/$t" ;; esac
    n=$((n+1))
  done
  [ ! -L "$root$p" ] && [ -f "$root$p" ] && [ -x "$root$p" ]
}

# wq#4524: every tool the stage4 manifest lists must be present under <root>: apt packages
# installed in its dpkg db, built binaries at their install paths, binary artifacts
# unpacked. Logs each miss and returns 1 if any.
# wq#4734: also fails if any catalog tool stage4_package_re_tools could not resolve at all
# is core-tier -- this used to only check tools that made it INTO the manifest's install
# sections, so a core tool gen_tool_packages.py reported fully unresolved (wrong trust tier,
# no apt package on this build host, no recipe) never failed the build; stage4 just logged
# the unresolved count and continued.
verify_re_tools() {
  local root="$1" miss=0 p bin dir core_unresolved
  [ -f "$TOOL_MANIFEST" ] || { log "no $TOOL_MANIFEST -- stage4_package_re_tools never ran against this WORKDIR"; return 1; }
  while read -r p; do
    [ -n "$p" ] || continue
    if [ "$(dpkg-query --admindir="$root/var/lib/dpkg" -W -f='${db:Status-Status}' "$p" 2>/dev/null)" != "installed" ]; then
      log "missing RE tool apt package: $p"; miss=1
    fi
  done < <(jq -r '.apt_packages[]' "$TOOL_MANIFEST")
  while read -r bin; do
    [ -n "$bin" ] || continue
    root_exec_ok "$root" "$bin" || { log "missing RE tool binary: $bin"; miss=1; }
  done < <(jq -r '((.cargo_crates + .pip_packages)[] | "/usr/local/bin/" + .bin), (.pip_git_packages[] | "/opt/re-venv/bin/" + .bin), ((.source_build_tools // [])[].bin), ((.binary_artifacts // [])[] | select(.bin) | "/usr/local/bin/" + .bin)' "$TOOL_MANIFEST")
  while read -r dir; do
    [ -n "$dir" ] || continue
    [ -n "$(ls -A "$root$dir" 2>/dev/null)" ] || { log "missing RE tool artifact dir: $dir"; miss=1; }
  done < <(jq -r '.binary_artifacts[].extract_to' "$TOOL_MANIFEST")
  core_unresolved="$(jq -r '[(.unresolved // [])[] | select(.trust == "core")] | length' "$TOOL_MANIFEST")"
  if [ "$core_unresolved" -gt 0 ]; then
    log "core-tier RE tool(s) unresolved at stage4 verify time ($core_unresolved; see $TOOL_MANIFEST .unresolved for the reason each one failed)"
    miss=1
  fi
  return "$miss"
}

# wq#4596: the live-only SSH design (wq#4548 option C) exists so sshd and its host keys
# never leave the live session; nothing in the production flow should put them in the
# target layer. Guards against a stale/mis-synced upper/ (or a future stage3/stage4
# change) shipping them anyway. LIMPET_ALLOW_SSHD_IN_TARGET_LAYER is an explicit,
# deliberate override for someone who has a real reason to ship sshd in the installed
# system -- never set by default.
verify_no_live_ssh() {
  local root="$1" hit=0 f
  [ -f "$root/usr/sbin/sshd" ] && { log "verify-no-live-ssh: $root/usr/sbin/sshd present"; hit=1; }
  for f in "$root"/etc/ssh/ssh_host_*_key; do
    [ -f "$f" ] || continue
    log "verify-no-live-ssh: $f present"; hit=1
  done
  return "$hit"
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
    docker run --rm -v "$out:/out" "ubuntu:$XUBUNTU_VERSION" bash -c \
      "set -eu; export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq --no-install-recommends cargo rustc build-essential pkg-config ca-certificates $bdeps; cargo install $crate --version $ver --locked --root /out 2>&1 || true; test -x /out/bin/$bin"
    [ -x "$out/bin/$bin" ] || fail "cargo build of $crate $ver produced no $out/bin/$bin"
    # wq#4673: Ubuntu 26.04's uutils coreutils `install` returns non-zero on an
    # existing target ("Already exists") instead of GNU's silent overwrite,
    # which kills this script under set -euo pipefail on a re-run.
    rm -f "$CHROOT/usr/local/bin/$bin"
    gnuinstall -m 0755 "$out/bin/$bin" "$CHROOT/usr/local/bin/$bin"
    log "stage4b: installed /usr/local/bin/$bin ($(sha256sum "$out/bin/$bin" | cut -c1-16)...)"
  done < <(jq -c '.cargo_crates[]' "$manifest")
}

# wq#4116: build recipe-backed pip tools in an ubuntu:$XUBUNTU_VERSION container
# and copy ONLY the resulting venv/pipx tree into the chroot, at the exact
# absolute path it is built at -- a venv's console-script shebang bakes in an
# absolute interpreter path, so the build path and the chroot-install path
# must be identical or the shebang breaks on first run. Two families, both
# validated against gen_tool_packages.py's PIP_RECIPES/PIP_GIT_RECIPES (never
# an unverified recipe reaches here):
#   manifest.pip_packages:     PyPI release, pipx-installed, sha256-verified
#                               before install (unblob, frida-tools).
#   manifest.pip_git_packages: pinned-commit git source, shared venv at
#                               /opt/re-venv (mtkclient, edl -- neither cuts
#                               PyPI releases; the commit is checked out
#                               --detach and the resulting HEAD is compared
#                               byte-for-byte against the pin before install,
#                               same pattern as driver_hook.py's DKMS builds).
stage4c_build_pip_tools() {
  local manifest="$1" out="$WORKDIR/pip-out"
  require_cmd docker; require_cmd jq
  rm -rf "$out"
  mkdir -p "$out/usr/local/share/pipx" "$out/usr/local/bin" "$out/opt/re-venv"
  mkdir -p "$CHROOT/usr/local/bin" "$CHROOT/usr/local/share/pipx" "$CHROOT/opt/re-venv"

  local c name package ver sha256 bin
  while read -r c; do
    name="$(jq -r .name <<<"$c")"; package="$(jq -r .package <<<"$c")"
    ver="$(jq -r .version <<<"$c")"; sha256="$(jq -r .sha256 <<<"$c")"
    bin="$(jq -r .bin <<<"$c")"
    log "stage4c: pipx install $package==$ver (container ubuntu:$XUBUNTU_VERSION, sha256-pinned)"
    # wq#4601: bind-mount straight onto /usr/local/share/pipx and /usr/local/bin --
    # the SAME absolute paths the chroot has -- instead of an /out-prefixed stand-in.
    # pipx bakes PIPX_HOME's absolute path into every venv shebang and app symlink it
    # creates, so building under /out/usr/local/... and then cp -a'ing into $CHROOT left
    # every console script pointing at an interpreter under a nonexistent /out tree.
    docker run --rm \
      -e PIPX_HOME=/usr/local/share/pipx \
      -e PIPX_BIN_DIR=/usr/local/bin \
      -v "$out/usr/local/share/pipx:/usr/local/share/pipx" \
      -v "$out/usr/local/bin:/usr/local/bin" \
      "ubuntu:$XUBUNTU_VERSION" bash -euc "
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y -qq --no-install-recommends python3-venv python3-pip pipx ca-certificates
      rm -rf /tmp/dl; mkdir -p /tmp/dl
      pip download --no-deps --dest /tmp/dl '$package==$ver'
      got=\"\$(sha256sum /tmp/dl/* | awk '{print \$1}' | head -1)\"
      [ \"\$got\" = '$sha256' ] || { echo \"stage4c: sha256 mismatch for $package==$ver: got \$got want $sha256\" >&2; exit 1; }
      export PIPX_HOME=/usr/local/share/pipx
      export PIPX_BIN_DIR=/usr/local/bin
      pipx install /tmp/dl/*
      # Fallback: if pipx put binaries in ~/.local/bin despite env vars, copy them.
      # wq#4676: every \$ and \" below MUST stay escaped -- this whole block is one
      # double-quoted string in build.sh's own shell, so an unescaped \$(...) runs on the
      # BUILD HOST at docker-run-assembly time (against the build user's ~/.local/bin,
      # before the container even starts) and an unescaped \" ends the string early,
      # letting the substitution's word-split output spill into docker's argv. The
      # bare-\$ form shipped in wq#4672 did exactly that; ~ and * need no escaping
      # (no tilde/glob expansion happens inside double quotes).
      if [ -d ~/.local/bin ] && [ -n \"\$(ls ~/.local/bin/ 2>/dev/null)\" ]; then
        cp -a ~/.local/bin/* /usr/local/bin/ 2>/dev/null || true
      fi
    "
    { [ -x "$out/usr/local/bin/$bin" ] || [ -L "$out/usr/local/bin/$bin" ]; } || fail "pipx install of $package $ver produced no $out/usr/local/bin/$bin"
    log "stage4c: built /usr/local/bin/$bin ($(sha256sum "$out/usr/local/bin/$bin" | cut -c1-16)...)"
  done < <(jq -c '.pip_packages[]' "$manifest")
  [ -d "$out/usr/local/share/pipx" ] && cp -a "$out/usr/local/share/pipx/." "$CHROOT/usr/local/share/pipx/"
  [ -d "$out/usr/local/bin" ] && cp -a "$out/usr/local/bin/." "$CHROOT/usr/local/bin/"

  local url commit bdeps
  while read -r c; do
    name="$(jq -r .name <<<"$c")"; url="$(jq -r .url <<<"$c")"; commit="$(jq -r .commit <<<"$c")"
    bin="$(jq -r .bin <<<"$c")"; bdeps="$(jq -r '.build_apt | join(" ")' <<<"$c")"
    log "stage4c: pip install $name from $url @ $commit (container ubuntu:$XUBUNTU_VERSION, pinned commit)"
    # Same fix as above: /opt/re-venv is bind-mounted at its real, final absolute path,
    # so `python3 -m venv` bakes a shebang that is still correct once copied into $CHROOT.
    docker run --rm -v "$out/opt/re-venv:/opt/re-venv" "ubuntu:$XUBUNTU_VERSION" bash -euc "
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y -qq --no-install-recommends python3-venv python3-pip python3-dev build-essential git ca-certificates $bdeps
      rm -rf /tmp/src
      git clone --no-checkout '${url#git+}' /tmp/src
      git -C /tmp/src checkout --detach '$commit'
      [ \"\$(git -C /tmp/src rev-parse HEAD)\" = '$commit' ] || { echo \"stage4c: checked-out HEAD != pinned commit for $name\" >&2; exit 1; }
      python3 -m venv /opt/re-venv
      /opt/re-venv/bin/pip install --no-cache-dir /tmp/src
    "
    [ -x "$out/opt/re-venv/bin/$bin" ] || fail "pip install of $name @ $commit produced no $out/opt/re-venv/bin/$bin"
    log "stage4c: built /opt/re-venv/bin/$bin ($(sha256sum "$out/opt/re-venv/bin/$bin" | cut -c1-16)...)"
  done < <(jq -c '.pip_git_packages[]' "$manifest")
  [ -d "$out/opt/re-venv" ] && cp -a "$out/opt/re-venv/." "$CHROOT/opt/re-venv/"
}

# wq#4116: fetch recipe-backed binary release artifacts (ghidra) on the build
# host and unpack them straight into the chroot. sha256-verified against
# gen_tool_packages.py's BINARY_RECIPES before any byte lands under $CHROOT --
# same fail-closed rule as stage4b/stage4c. The JDK dependency Ghidra 12.x
# declares (Ghidra/application.properties application.java.min=21) rides in
# through manifest.apt_packages (openjdk-21-jre-headless), installed by the
# stage4 apt-get call above this one runs after.
stage4d_fetch_binary_tools() {
  local manifest="$1" out="$WORKDIR/binary-out"
  require_cmd curl; require_cmd jq; require_cmd sha256sum; require_cmd unzip
  mkdir -p "$out" "$out/usr/local/bin"
  local c name artifact url sha256 extract_to bin got top
  while read -r c; do
    name="$(jq -r .name <<<"$c")"; artifact="$(jq -r .artifact <<<"$c")"
    url="$(jq -r .url <<<"$c")"; sha256="$(jq -r .sha256 <<<"$c")"
    extract_to="$(jq -r .extract_to <<<"$c")"; bin="$(jq -r '.bin // ""' <<<"$c")"
    log "stage4d: fetch $name ($artifact, sha256-pinned)"
    curl -fsSL "$url" -o "$out/$artifact"
    got="$(sha256sum "$out/$artifact" | cut -d' ' -f1)"
    [ "$got" = "$sha256" ] || fail "sha256 mismatch for $artifact: got $got want $sha256"
    rm -rf "$out/extract-$name"; mkdir -p "$out/extract-$name" "$CHROOT$extract_to"
    unzip -q "$out/$artifact" -d "$out/extract-$name"
    # wq#4734: a zip with exactly one top-level directory (ghidra) is a wrapper to unwrap
    # one level; a zip with no such wrapper (jadx's release zip extracts straight to
    # LICENSE/README.md/bin/lib at the top level) must be copied as-is -- unwrapping the
    # first -type d match in that case silently dropped every sibling entry.
    local top_entries top_dirs
    top_entries="$(find "$out/extract-$name" -mindepth 1 -maxdepth 1 | wc -l)"
    top_dirs="$(find "$out/extract-$name" -mindepth 1 -maxdepth 1 -type d | wc -l)"
    if [ "$top_entries" -eq 1 ] && [ "$top_dirs" -eq 1 ]; then
      top="$(find "$out/extract-$name" -mindepth 1 -maxdepth 1 -type d)"
      cp -a "$top/." "$CHROOT$extract_to/"
    else
      cp -a "$out/extract-$name/." "$CHROOT$extract_to/"
    fi
    log "stage4d: installed $extract_to from $artifact (sha256 ${got:0:16}...)"
    # wq#4809: ghidra unzips a launcher (ghidraRun) into extract_to, but nothing put
    # it on PATH -- for ghidra this resolves to `ln -s /opt/ghidra/ghidraRun
    # /usr/local/bin/ghidraRun`. Mirror stage4c's out-then-cp pattern (build the
    # symlink under $out, bulk-copy into $CHROOT at the end) rather than writing
    # into $CHROOT mid-loop, so it lands the same way every other stage4d artifact
    # does and survives the upper/ layer pack.
    if [ -n "$bin" ]; then
      ln -sfn "$extract_to/$bin" "$out/usr/local/bin/$bin"
      log "stage4d: linked /usr/local/bin/$bin -> $extract_to/$bin"
    fi
  done < <(jq -c '.binary_artifacts[]' "$manifest")
  [ -d "$out/usr/local/bin" ] && cp -a "$out/usr/local/bin/." "$CHROOT/usr/local/bin/"
}

# wq#4734: fetch recipe-backed source tools (searchsploit) that ship no packaged release --
# apt's 'exploitdb' is Kali-only and absent on Ubuntu 26.04. Pulled as a sha256-pinned GitLab
# archive of the single commit gen_tool_packages.py's SOURCE_BUILD_RECIPES/catalog 'commit'
# field names (never a moving branch), verified before anything lands under $CHROOT, same
# fail-closed rule as stage4b/c/d.
stage4e_build_source_tools() {
  local manifest="$1" out="$WORKDIR/source-build-out"
  require_cmd curl; require_cmd jq; require_cmd sha256sum; require_cmd tar
  mkdir -p "$out"
  local c name repo commit archive_name sha256 extract_to bin_src bin url got
  while read -r c; do
    name="$(jq -r .name <<<"$c")"; repo="$(jq -r .repo <<<"$c")"
    commit="$(jq -r .commit <<<"$c")"; archive_name="$(jq -r .archive_name <<<"$c")"
    sha256="$(jq -r .sha256 <<<"$c")"; extract_to="$(jq -r .extract_to <<<"$c")"
    bin_src="$(jq -r .bin_src <<<"$c")"; bin="$(jq -r .bin <<<"$c")"
    url="$repo/-/archive/$commit/$archive_name-$commit.tar.gz"
    log "stage4e: fetch $name ($archive_name@${commit:0:12}, sha256-pinned)"
    curl -fsSL "$url" -o "$out/$name.tar.gz"
    got="$(sha256sum "$out/$name.tar.gz" | cut -d' ' -f1)"
    [ "$got" = "$sha256" ] || fail "sha256 mismatch for $name source archive: got $got want $sha256"
    rm -rf "$out/extract-$name"; mkdir -p "$out/extract-$name" "$CHROOT$extract_to"
    tar -xzf "$out/$name.tar.gz" -C "$out/extract-$name" --strip-components=1
    cp -a "$out/extract-$name/." "$CHROOT$extract_to/"
    chroot "$CHROOT" chmod +x "$extract_to/$bin_src"
    chroot "$CHROOT" ln -sf "$extract_to/$bin_src" "$bin"
    log "stage4e: installed $bin -> $extract_to/$bin_src from $name (sha256 ${got:0:16}...)"
  done < <(jq -c '.source_build_tools[]' "$manifest")
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
  n="$(find "$LIMPET_ROOT/hardware-book" -name '*.toml' | wc -l)"
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
  # kernel other than driver_hook/kernel-<release>.pin, or (exit 4) on an unpinned DKMS source.
  # LIMPET_DRIVER_HOOK_RUN=1 additionally executes the in-chroot installer (needs root +
  # network inside the chroot): firmware apt packages and any pinned DKMS builds.
  local run=()
  [ "${LIMPET_DRIVER_HOOK_RUN:-0}" = "1" ] && run=(--run-chroot)
  python3 -I "$HERE/driver_hook/driver_hook.py" install --release "$XUBUNTU_VERSION" --chroot "$CHROOT" --bundle "$bundle" "${run[@]}"
  log "stage7: driver bundle installed for the pinned kernel ABI (pin: $KERNEL_PIN; see README "Driver bundle hook")"
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
# Stage 10 -- serial console
# ---------------------------------------------------------------------------
# wq#4526: limpet-serial.log came back empty from QEMU boots because the
# kernel was never told to print to ttyS0. Only /etc/default/grub is edited
# here: the live ISO boot reads the extracted ISO's own boot/grub/grub.cfg
# (iso_layers.py grub-cfg bakes SERIAL_ARGS into that file directly, see
# build.sh's "for cfg in grub.cfg loopback.cfg" loop below), and an installed
# system's installer runs its own update-grub from /etc/default/grub on the
# target disk. Running update-grub here, inside the stage1b overlayfs chroot,
# produces a grub.cfg nothing consumes and fails anyway: grub-probe cannot
# map / (overlay) to a block device (wq#4740, ISSUE-LOG row 1620).
stage10_configure_serial_console() {
  log "stage10: serial console"
  [ -d "$CHROOT" ] || fail "chroot not assembled yet (run stage1_fetch_base_iso then stage1b_assemble_chroot)"
  require_cmd python3

  python3 -I "$HERE/set_grub_cmdline.py" --default "$CHROOT/etc/default/grub"

  log "stage10: added console=tty0 console=ttyS0,115200n8 to GRUB_CMDLINE_LINUX"
}

# Append the snap:NAME<TAB>channel<TAB>revision records of an original manifest.full to a
# regenerated one (dpkg-query cannot list snaps). No snap lines is fine.
carry_snap_records() {
  [ -f "$1" ] || return 0
  grep '^snap:' "$1" >>"$2" || true
}

# ---------------------------------------------------------------------------
# Live-only test SSH (wq#4558 option C; replaces wq#4527's stage3b)
# ---------------------------------------------------------------------------
# Opt-in: only when LIMPET_TEST_SSH_PUBKEY is set. Installs openssh-server into the
# LIVE layer only, key-only, so the validation ISO is the production ISO plus a
# live-session sshd. minimal.standard (the layer the installer copies to disk) is never
# touched, so nothing SSH-related can reach an installed system.
# Runs inside repack_live_layer, after the dpkg database merge: the chroot is an overlay
# whose lowers are the MODIFIED target layer and its parents and whose upper is the live
# layer tree, so dpkg sees the merged database and writes a consistent one back into
# the live layer. apt lists and cache go to the chroot tmpfs (/run), not into the layer.
install_live_test_ssh() {
  local layer="$1" lw="$2" pubkey="$3" casper="$4"
  [ -s "$pubkey" ] || fail "live-ssh: LIMPET_TEST_SSH_PUBKEY=$pubkey is missing or empty"
  if grep -q 'PRIVATE KEY' "$pubkey"; then
    fail "live-ssh: $pubkey contains a PRIVATE key -- LIMPET_TEST_SSH_PUBKEY must be the .pub file"
  fi
  require_cmd mount
  require_cmd umount
  require_cmd mountpoint
  require_cmd chroot
  require_cmd systemctl
  require_cmd mknod
  local ov="$WORKDIR/live-chroot" work="$WORKDIR/live-work" lowers d rc
  local lower_paths=() ours=() m
  # Regenerable caches and logs that apt/dpkg hooks (man-db, appstream, apt itself) write during an
  # install. They live in tmpfs for the duration, so they never reach the layer; hiding them only
  # affects this chroot, the lower layers' copies are untouched. var/cache/debconf is NOT masked:
  # it holds real package configuration state.
  local masks=(var/cache/apt var/cache/man var/cache/swcatalog var/lib/swcatalog var/log)
  # Lists, cache and sources all live in the chroot tmpfs (/run): nothing bulky enters the layer, and
  # the base image's file:/cdrom source (cdrom.sources, no Release file) cannot break apt-get update.
  local apt_opts=(-o Dir::State::lists=/run/apt-lists -o Dir::Cache=/run/apt-cache
                  -o Dir::Etc::sourceparts=/run/apt-sources.d -o Dir::Etc::sourcelist=/dev/null)
  lowers="$(iso_layers lowerdirs --layer "$layer" --root "$LAYERS_DIR/lower")"
  IFS=: read -r -a lower_paths <<<"$lowers"
  rm -rf "$work"
  mkdir -p "$ov" "$work"
  for d in "${lower_paths[@]}"; do mountpoint -q "$d" || ours+=("$d"); done
  log "live-ssh: installing openssh-server into the live layer only (key-only auth, key from $pubkey)"
  # No errexit leak: a plain subshell run under `set +e`, so a failing step really stops
  # the sequence and cleanup below always runs.
  set +e
  (
    set -e
    for d in "${ours[@]}"; do
      mkdir -p "$d"
      mount -t squashfs -o loop,ro "$casper/$(basename "$d").squashfs" "$d"
    done
    mount -t overlay overlay -o "lowerdir=$LAYERS_DIR/upper:$lowers,upperdir=$lw,workdir=$work" "$ov"
    mount --make-private "$ov"
    mount -t proc proc "$ov/proc"
    mount -t sysfs sysfs "$ov/sys"
    populate_chroot_dev "$ov"
    mount -t tmpfs tmpfs "$ov/run"
    for m in "${masks[@]}"; do
      mkdir -p "$ov/$m"
      mount -t tmpfs tmpfs "$ov/$m"
    done
    mkdir -p "$ov/run/systemd/resolve"
    cp -L /etc/resolv.conf "$ov/run/systemd/resolve/stub-resolv.conf"
    printf '#!/bin/sh\nexit 101\n' >"$ov/usr/sbin/policy-rc.d"
    chmod +x "$ov/usr/sbin/policy-rc.d"
    # apt will not create these itself: it fails with "List directory .../partial is missing".
    # /run is the chroot tmpfs, so none of this ends up in the layer.
    mkdir -p "$ov/run/apt-lists/partial" "$ov/run/apt-cache/archives/partial"
    # Only the archive source the image is configured with (not hardcoded here).
    [ -f "$ov/etc/apt/sources.list.d/ubuntu.sources" ] || { echo "no /etc/apt/sources.list.d/ubuntu.sources in the chroot" >&2; exit 1; }
    mkdir -p "$ov/run/apt-sources.d"
    cp "$ov/etc/apt/sources.list.d/ubuntu.sources" "$ov/run/apt-sources.d/"
    # The base layers carry stale apt lists, so refresh them -- into /run, not the layer.
    chroot "$ov" apt-get "${apt_opts[@]}" update -qq
    chroot "$ov" apt-get "${apt_opts[@]}" install -y openssh-server
    install -d -m 0755 "$ov/etc/ssh/sshd_config.d"
    printf '%s\n' "# wq#4558: test ISO, key-only" "PasswordAuthentication no" "KbdInteractiveAuthentication no" \
      "PermitRootLogin no" "PubkeyAuthentication yes" >"$ov/etc/ssh/sshd_config.d/10-limpet-test.conf"
    # casper creates the live user from /etc/skel at boot, so the key rides along whatever
    # username that is (ubuntu per the base casper.conf; not hardcoded here).
    install -d -m 0700 "$ov/etc/skel/.ssh"
    gnuinstall -m 0600 "$pubkey" "$ov/etc/skel/.ssh/authorized_keys"
    systemctl --root="$ov" enable ssh.service
  )
  rc=$?
  set -e
  # Always: drop the build-time guard, unmount in reverse order, unmount only what we mounted.
  rm -f "$ov/usr/sbin/policy-rc.d"
  for m in "${masks[@]}" dev/pts dev sys proc run; do mountpoint -q "$ov/$m" && umount "$ov/$m"; done
  mountpoint -q "$ov" && umount "$ov"
  for d in "${ours[@]}"; do mountpoint -q "$d" && umount "$d"; done
  rm -rf "$work"
  [ "$rc" -eq 0 ] || fail "live-ssh: installing the live-only sshd failed (rc=$rc) -- see above; not shipping a half-configured live layer"
  log "live-ssh: openssh-server installed in the live layer, ssh.service enabled, key-only auth"
}

# ---------------------------------------------------------------------------
# Live layer repack (wq#4558, research: reports/wq4557_live_layer_dpkg_consistency.md)
# ---------------------------------------------------------------------------
# casper stacks minimal -> minimal.standard -> minimal.standard.live with the live
# layer on top. The live layer carries its own FULL var/lib/dpkg/status, so on the
# stock ISO it shadows the target layer's database: packages this build adds look
# uninstalled, packages it removes look installed (measured and observed in a
# booted guest). Repack the live layer with a status merged against the target
# layer as it is after this build. Always merges from the ORIGINAL base layers
# (never from a previous repack), so rebuilds are idempotent. Fails closed on a
# merge conflict. Not covered here (not checked): dpkg diversions, alternatives and
# trigger state files the live layer also carries.
repack_live_layer() {
  local layer="$1" changes="$2" casper="$3" live="$1.live"
  case "$layer" in
    *.live)
      [ -z "${LIMPET_TEST_SSH_PUBKEY:-}" ] || fail "live-ssh: LIMPET_TEST_SSH_PUBKEY is set but the target layer $layer is itself the live layer -- test SSH only goes into a live layer ABOVE the target; unset LIMPET_ISO_LAYER"
      log "live-repack: $layer is itself the live layer -- nothing separate to repack"
      return 0 ;;
  esac
  if [ ! -f "$casper/$live.squashfs" ]; then
    [ -z "${LIMPET_TEST_SSH_PUBKEY:-}" ] || fail "live-ssh: LIMPET_TEST_SSH_PUBKEY is set but the base ISO has no $live.squashfs to put it in"
    log "live-repack: no $live.squashfs in the base ISO -- nothing to repack"
    return 0
  fi
  [ -f "$WORKDIR/modified.dpkg-status" ] || fail "live-repack: $WORKDIR/modified.dpkg-status missing (stage11 saves it before releasing the chroot)"
  require_cmd unsquashfs
  require_cmd mksquashfs
  require_cmd dpkg-query
  local lw="$WORKDIR/live-upper" st="$WORKDIR/live-state"
  rm -rf "$lw" "$st"
  mkdir -p "$st/admin/info" "$st/admin/updates" "$changes/casper"
  log "live-repack: $live gets a dpkg database merged against the modified $layer"
  unsquashfs -d "$lw" "$casper/$live.squashfs" >/dev/null
  [ -f "$lw/var/lib/dpkg/status" ] || fail "live-repack: $live has no var/lib/dpkg/status -- cannot merge"
  unsquashfs -cat "$casper/$layer.squashfs" var/lib/dpkg/status >"$st/base.status" \
    || fail "live-repack: cannot read the original $layer dpkg status"
  unsquashfs -cat "$casper/$layer.squashfs" var/lib/apt/extended_states >"$st/base.ext" 2>/dev/null || : >"$st/base.ext"
  cp "$lw/var/lib/dpkg/status" "$st/live.status"
  if [ -f "$lw/var/lib/apt/extended_states" ]; then cp "$lw/var/lib/apt/extended_states" "$st/live.ext"; else : >"$st/live.ext"; fi
  iso_layers merge-dpkg-status --base "$st/base.status" --live "$st/live.status" \
    --modified "$WORKDIR/modified.dpkg-status" --out "$st/merged.status" \
    || fail "live-repack: dpkg status merge for $live failed -- see above; not shipping a live layer that disagrees with its parent"
  iso_layers merge-extended-states --base "$st/base.ext" --live "$st/live.ext" \
    --modified "$WORKDIR/modified.extended_states" --status "$st/merged.status" --out "$st/merged.ext" \
    || fail "live-repack: extended_states merge for $live failed -- see above"
  gnuinstall -m 0644 "$st/merged.status" "$lw/var/lib/dpkg/status"
  if [ -s "$st/merged.ext" ] || [ -f "$lw/var/lib/apt/extended_states" ]; then
    mkdir -p "$lw/var/lib/apt"
    gnuinstall -m 0644 "$st/merged.ext" "$lw/var/lib/apt/extended_states"
  fi

  if [ -n "${LIMPET_TEST_SSH_PUBKEY:-}" ]; then
    install_live_test_ssh "$layer" "$lw" "$LIMPET_TEST_SSH_PUBKEY" "$casper"
  fi

  # Metadata livecd-rootfs derives per layer: manifest.full = every package up to
  # this layer, the delta manifest = what it adds over its (NEW) parent, size = footprint.
  cp "$lw/var/lib/dpkg/status" "$st/admin/status"   # final: includes openssh-server when test SSH was installed
  dpkg-query --show --admindir="$st/admin" >"$changes/casper/$live.manifest.full" \
    || fail "live-repack: dpkg-query failed on the merged status"
  # same snap records as above, from the original live layer (it lists the installer snaps too)
  carry_snap_records "$casper/$live.manifest.full" "$changes/casper/$live.manifest.full"
  diff -NU0 "$changes/casper/$layer.manifest.full" "$changes/casper/$live.manifest.full" \
    | grep -v '^@' >"$changes/casper/$live.manifest" || true
  du -B 1 -s "$lw" | cut -f1 >"$changes/casper/$live.size"

  local comp bs
  comp="$(unsquashfs -s "$casper/$live.squashfs" | awk '/^Compression/{print $2}')"
  bs="$(unsquashfs -s "$casper/$live.squashfs" | awk '/^Block size/{print $3}')"
  mksquashfs "$lw" "$changes/casper/$live.squashfs" -noappend -comp "$comp" -b "$bs" -xattrs -no-progress >/dev/null
  rm -rf "$lw"
  log "live-repack: wrote $live.squashfs + manifest, manifest.full, size (merged dpkg database)"
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
  # wq#4524: check upper/ itself, the tree that becomes the squashfs, so a skipped stage4
  # or installs that went to some other mount fail here instead of shipping an ISO
  # without RE tools.
  require_cmd jq
  verify_re_tools "$upper" || fail "refusing to pack $upper without the stage4 RE tools (see above)"
  # wq#4596: sshd/host keys in the target layer ship into the INSTALLED system, which the
  # live-only SSH design (wq#4548 option C) exists to prevent.
  if [ -z "${LIMPET_ALLOW_SSHD_IN_TARGET_LAYER:-}" ]; then
    verify_no_live_ssh "$upper" ||
      fail "refusing to pack $upper: it contains sshd and/or host keys (see above) -- move it aside and rerun: mv '$upper' '$upper.stale-$(date +%Y%m%d_%H%M%S)', or set LIMPET_ALLOW_SSHD_IN_TARGET_LAYER=1 if you really mean to ship sshd in the installed system"
  fi
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
  # dpkg-query only knows dpkg packages; livecd-rootfs also records seeded snaps in
  # manifest.full (snap:NAME<TAB>channel<TAB>revision). Carry the original's over: this
  # build never touches the snap seed, and dropping them rewrote the layer's metadata.
  carry_snap_records "$casper/$layer.manifest.full" "$changes/casper/$layer.manifest.full"
  diff -NU0 "$casper/$parent.manifest.full" "$changes/casper/$layer.manifest.full" \
    | grep -v '^@' >"$changes/casper/$layer.manifest" || true
  local merged_size
  merged_size="$(du -B 1 -s -x "$CHROOT/" | cut -f1)"
  # wq#4558: the live layer is merged against the target layer as it stands now.
  cp "$CHROOT/var/lib/dpkg/status" "$WORKDIR/modified.dpkg-status"
  if [ -f "$CHROOT/var/lib/apt/extended_states" ]; then
    cp "$CHROOT/var/lib/apt/extended_states" "$WORKDIR/modified.extended_states"
  else
    : >"$WORKDIR/modified.extended_states"
  fi
  release_chroot
  du -B 1 -s "$upper" | cut -f1 >"$changes/casper/$layer.size"

  # Same compression + block size as the layer being replaced.
  local comp bs
  comp="$(unsquashfs -s "$casper/$layer.squashfs" | awk '/^Compression/{print $2}')"
  bs="$(unsquashfs -s "$casper/$layer.squashfs" | awk '/^Block size/{print $3}')"
  mksquashfs "$upper" "$changes/casper/$layer.squashfs" -noappend -comp "$comp" -b "$bs" -xattrs -no-progress >/dev/null

  repack_live_layer "$layer" "$changes" "$casper"

  # install, not cp: xorriso extracts ISO files 0444 and these get rewritten.
  gnuinstall -m 0644 "$casper/install-sources.yaml" "$changes/casper/"
  iso_layers set-size --sources "$changes/casper/install-sources.yaml" --layer "$layer" --size "$merged_size"

  # The ISO's own boot menu (distinct from the installed system's grub, which
  # stage9 themes and stage10 points at ttyS0). Rebranded from the stock
  # entries; iso_layers.py grub-cfg also adds the serial console kernel args
  # here (wq#4534) -- stage12 boots this ISO directly, so without this the
  # serial log stays empty even though stage10 fixed the installed system.
  local cfg
  for cfg in grub.cfg loopback.cfg; do
    gnuinstall -m 0644 "$ISO_EXTRACT/boot/grub/$cfg" "$changes/boot/grub/"
    iso_layers grub-cfg --file "$changes/boot/grub/$cfg"
  done

  local rel=()
  mapfile -t rel < <(cd "$changes" && find . -type f | sed 's|^\./||' | sort)
  gnuinstall -m 0644 "$ISO_EXTRACT/md5sum.txt" "$changes/md5sum.txt"
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
# Stage 11b -- installer presence gate (wq#4523)
# ---------------------------------------------------------------------------
# limpet-20261021.iso shipped with no working installer (P0). Xubuntu 26.04's
# installer is NOT ubiquity (retired upstream; absent from the stock ISO too --
# research/WQ3983_XUBUNTU_26_10_DISSECTION.md) but the ubuntu-desktop-bootstrap
# snap, seeded in the live-only casper layer. This gate probes the finished
# ISO -- not the build inputs, which wq#4456 proved can drift -- for everything
# that installer needs: install-sources.yaml, every referenced layer, the live
# layer, and the snap seed inside it. A missing seed is exactly the ghost
# "Install" icon the wq#4523 validation hit.
stage11b_verify_installer() {
  log "stage11b: verifying the stock installer survived the remaster"
  require_cmd python3
  require_cmd xorriso
  require_cmd unsquashfs
  local iso="$OUT_DIR/$ISO_NAME"
  [ -f "$iso" ] || fail "no ISO at $iso -- run stage11 first"
  python3 -I "$HERE/verify_installer.py" --iso "$iso" \
    || fail "installer verification failed on $iso -- see the errors above; this ISO cannot install itself"
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
  stage3c_install_base_utils
  stage4_package_re_tools
  stage5_bundle_hardware_book
  stage6_bundle_target_device_db
  stage7_bundle_driver_bundle
  stage8_bundle_exploit_db
  stage9_apply_branding
  stage10_configure_serial_console
  stage11_build_hybrid_iso
  stage11b_verify_installer
  stage12_qemu_test
)

usage() {
  cat <<EOF
Usage: $0 [stage-name|all|teardown]

Stages (run in this order for 'all'):
$(printf '  %s\n' "${STAGES[@]}")

  teardown  unmount everything left under WORKDIR (wq#4710) -- run this before
            removing WORKDIR by hand, and after a build was killed/failed and
            you want to retry stage1b from scratch instead of reusing its mounts

Env:
  LIMPET_XUBUNTU_VERSION  base release (default 26.10; 26.04 also pinned) -- selects
                          driver_hook/kernel-<release>.pin and the ubuntu:<release> containers
  LIMPET_BASE_ISO         path to a cached Xubuntu $XUBUNTU_VERSION ISO (stage1)
  LIMPET_BASE_ISO_SHA256  its published sha256 (stage1; default and required value: the pin's)
  LIMPET_ISO_LAYER        casper layer to modify (default: the install-sources.yaml
                          default source, minimal.standard on stock 26.04 and 26.10)
  LIMPET_ISO_WORKDIR      scratch dir (default: $HERE/.build)
  LIMPET_ISO_OUT_DIR      output dir (default: \$WORKDIR/out)
  LIMPET_ISO_NAME         output filename (default: limpet-<date>.iso)
  LIMPET_TOOL_CATALOG     RE tool catalog for stage4 (default: \$LIMPET_ROOT/tool-catalog.json)
  LIMPET_TEST_SSH_PUBKEY  public key for key-only SSH in the LIVE layer of a test ISO (stage11 live repack, wq#4558);
                          unset = no SSH anywhere. Never reaches minimal.standard or an installed system.
  LIMPET_ALLOW_SSHD_IN_TARGET_LAYER
                          override stage11's refusal to pack a target layer upper/ containing
                          sshd/host keys (wq#4596). Unset by default; nothing in production sets it.
EOF
}

# wq#4771: a full build runs in its own mount namespace with private propagation, so
# no mount or unmount it makes can reach the host's mount table, and the kernel drops
# every build mount when the namespace's last process exits, whatever killed it.
# Single stages stay in the host namespace on purpose: stage1b's mounts must outlive
# the invocation for the next stage to resume from (see _teardown_on_exit).
# Re-execs at most once: our namespace differing from PID 1's proves the isolation,
# and LIMPET_ISO_MOUNT_NS breaks the loop if unshare somehow didn't provide it.
enter_private_mount_ns() {
  local ours init
  ours="$(readlink /proc/self/ns/mnt 2>/dev/null || true)"
  init="$(readlink /proc/1/ns/mnt 2>/dev/null || true)"
  if [ -n "$ours" ] && [ -n "$init" ] && [ "$ours" != "$init" ]; then
    log "all: running in private mount namespace $ours (wq#4771)"
    return 0
  fi
  [ -z "${LIMPET_ISO_MOUNT_NS:-}" ] || fail "all: re-executed under unshare but still not in a private mount namespace (self=$ours init=$init)"
  [ "$(id -u)" -eq 0 ] || fail "'all' must run as root (sudo -E $0 all) -- it re-executes itself under unshare -m so no build mount can reach the host (wq#4771)"
  require_cmd unshare
  log "all: re-executing under unshare --mount --propagation private (wq#4771)"
  trap - EXIT INT TERM
  exec env LIMPET_ISO_MOUNT_NS=1 unshare --mount --propagation private -- bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"
}

main() {
  local target="${1:-}"
  mkdir -p "$WORKDIR"
  if [ -z "$target" ] || [ "$target" = "-h" ] || [ "$target" = "--help" ]; then
    usage; exit 0
  fi
  if [ "$target" = "all" ]; then
    enter_private_mount_ns "$@"
    for s in "${STAGES[@]}"; do "$s"; done
    return
  fi
  if [ "$target" = "teardown" ]; then
    stage_teardown; return
  fi
  for s in "${STAGES[@]}"; do
    if [ "$s" = "$target" ]; then "$s"; return; fi
  done
  fail "unknown stage: $target (see --help)"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
