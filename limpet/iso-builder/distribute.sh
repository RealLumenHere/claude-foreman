#!/usr/bin/env bash
# wq#4604 -- Limpet ISO phase 4: torrent distribution. Takes the ISO that
# build.sh's stage11 already wrote to $OUT_DIR and produces everything
# docs/deployment-spec.md section 1 "Integrity"/"Torrent" promises: a
# .torrent (opentrackr tracker + DHT, no -p so peers/DHT stay enabled),
# SHA256SUMS + SHA512SUMS, a detached GPG signature over SHA256SUMS, a
# magnet link, a seeding transmission-cli instance on this box, and the
# GitHub release notes body.
#
# The magnet link is derived locally from the .torrent's BEP-3 info-hash
# (release.py) rather than trusted to a `transmission-show -m` flag whose
# availability varies by packaged version -- verify, don't guess.
#
# stage_install / stage_seed / stage_sign run privileged or host-stateful
# commands (apt, systemd-run, gpg) this script does not assume it may run
# unattended with; each is independently runnable so an operator with the
# right access can run just that stage. See iso-builder/README.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OUT_DIR="${LIMPET_ISO_OUT_DIR:-$HERE/.build/out}"
ISO_NAME="${LIMPET_ISO_NAME:-}"
TRACKERS=("udp://tracker.opentrackr.org:1337/announce")
SEED_UNIT="${LIMPET_SEED_UNIT:-limpet-torrent-seed}"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
fail() { log "FAIL: $*"; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"; }

iso_path() {
  [ -n "$ISO_NAME" ] || fail "LIMPET_ISO_NAME not set -- point it at the ISO build.sh produced in \$LIMPET_ISO_OUT_DIR"
  local p="$OUT_DIR/$ISO_NAME"
  [ -f "$p" ] || fail "no ISO at $p -- run build.sh stage11_build_hybrid_iso first"
  printf '%s' "$p"
}

stage_install_transmission() {
  if command -v transmission-create >/dev/null 2>&1 && command -v transmission-cli >/dev/null 2>&1; then
    log "stage_install_transmission: already present ($(command -v transmission-create))"
    return
  fi
  log "stage_install_transmission: installing transmission-cli"
  sudo -n apt-get update
  sudo -n apt-get install -y transmission-cli
  require_cmd transmission-create
  require_cmd transmission-cli
}

stage_make_torrent() {
  require_cmd transmission-create
  local iso; iso="$(iso_path)"
  local torrent="$iso.torrent"
  log "stage_make_torrent: $torrent"
  rm -f "$torrent"
  local cmd=(transmission-create -o "$torrent" -c "Limpet -- the universal RE appliance ISO")
  for t in "${TRACKERS[@]}"; do cmd+=(-t "$t"); done
  cmd+=("$iso")
  "${cmd[@]}"
  [ -f "$torrent" ] || fail "transmission-create did not produce $torrent"
  log "stage_make_torrent: wrote $torrent"
}

stage_seed() {
  require_cmd transmission-cli
  local iso; iso="$(iso_path)"
  local torrent="$iso.torrent"
  [ -f "$torrent" ] || fail "no $torrent -- run stage_make_torrent first"
  log "stage_seed: starting detached unit $SEED_UNIT"
  sudo -n systemd-run \
    "--unit=$SEED_UNIT" \
    --collect \
    --description="Limpet ISO torrent seed" \
    transmission-cli -w "$OUT_DIR" "$torrent"
  log "stage_seed: launched -- check with: systemctl status $SEED_UNIT"
}

stage_checksums() {
  local iso; iso="$(iso_path)"
  local iso_base torrent_base
  iso_base="$(basename "$iso")"
  torrent_base="$iso_base.torrent"
  log "stage_checksums: SHA256SUMS + SHA512SUMS in $OUT_DIR"
  (
    cd "$OUT_DIR"
    [ -f "$torrent_base" ] || fail "no $torrent_base -- run stage_make_torrent first"
    sha256sum "$iso_base" "$torrent_base" > SHA256SUMS
    sha512sum "$iso_base" > SHA512SUMS
  )
  log "stage_checksums: wrote $OUT_DIR/SHA256SUMS and $OUT_DIR/SHA512SUMS"
}

stage_sign() {
  require_cmd gpg
  local key_id="${LIMPET_RELEASE_GPG_KEY_ID:-}"
  [ -n "$key_id" ] || fail "LIMPET_RELEASE_GPG_KEY_ID not set -- supply the Limpet release signing key's ID, do not guess one (docs/deployment-spec.md:66 'the Limpet release key')"
  [ -f "$OUT_DIR/SHA256SUMS" ] || fail "no $OUT_DIR/SHA256SUMS -- run stage_checksums first"
  log "stage_sign: detached-signing SHA256SUMS with $key_id"
  (
    cd "$OUT_DIR"
    gpg --batch --yes --local-user "$key_id" --detach-sign --output SHA256SUMS.gpg SHA256SUMS
  )
  log "stage_sign: wrote $OUT_DIR/SHA256SUMS.gpg"
}

stage_release_notes() {
  require_cmd python3
  local iso; iso="$(iso_path)"
  local iso_base torrent version magnet key_id
  iso_base="$(basename "$iso")"
  torrent="$OUT_DIR/$iso_base.torrent"
  [ -f "$torrent" ] || fail "no $torrent -- run stage_make_torrent first"
  # limpet-<version>-amd64.iso / limpet-<YYYYMMDD>.iso -- either way, the part
  # between "limpet-" and ".iso" is the version/build tag.
  version="${iso_base#limpet-}"; version="${version%.iso}"
  key_id="${LIMPET_RELEASE_GPG_KEY_ID:-<unset -- set LIMPET_RELEASE_GPG_KEY_ID before publishing>}"

  local tracker_args=()
  for t in "${TRACKERS[@]}"; do tracker_args+=(--tracker "$t"); done

  magnet="$(python3 "$HERE/release.py" magnet --torrent "$torrent" --name "$iso_base" "${tracker_args[@]}")"
  printf '%s' "$magnet" > "$OUT_DIR/MAGNET.txt"

  python3 "$HERE/release.py" notes \
    --version "$version" --iso-name "$iso_base" --magnet "$magnet" --key-id "$key_id" \
    "${tracker_args[@]}" \
    > "$OUT_DIR/RELEASE_NOTES.md"
  log "stage_release_notes: wrote $OUT_DIR/MAGNET.txt and $OUT_DIR/RELEASE_NOTES.md"
}

STAGES=(
  stage_install_transmission
  stage_make_torrent
  stage_seed
  stage_checksums
  stage_sign
  stage_release_notes
)

usage() {
  cat <<EOF
Usage: $0 [stage-name|all]

Stages (run in this order for 'all'):
$(printf '  %s\n' "${STAGES[@]}")

Env:
  LIMPET_ISO_OUT_DIR          dir holding the built ISO (default: $HERE/.build/out)
  LIMPET_ISO_NAME             the ISO filename build.sh produced (required)
  LIMPET_RELEASE_GPG_KEY_ID   Limpet release signing key ID (required for stage_sign)
  LIMPET_SEED_UNIT            systemd transient unit name for stage_seed (default: limpet-torrent-seed)

stage_install_transmission, stage_seed, and stage_sign run sudo/systemd-run/gpg
and are not run by the sandboxed build worker that wrote this script -- an
operator with that access runs them on Lumen directly. stage_make_torrent,
stage_checksums, and stage_release_notes need no elevated privilege.
EOF
}

main() {
  local target="${1:-}"
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

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
