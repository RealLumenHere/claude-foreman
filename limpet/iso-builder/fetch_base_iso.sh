#!/usr/bin/env bash
# wq#4008 -- gated Xubuntu base-ISO fetcher for build.sh's stage1 input.
#
# Root cause this replaces: a prior ad hoc fetch (outside this repo, under
# /home/lumen/scratch/xubuntu-26.10/) wrote a ".xubuntu_download_complete"
# marker and an "download complete" log line even though all four HTTP
# requests (desktop ISO, minimal ISO, manifest, SHA256SUMS) came back 404 --
# the release path under cdimage.ubuntu.com/xubuntu/releases/<ver>/release/
# does not exist until GA. The marker was written unconditionally after the
# fetch loop ran, not after the artifacts were verified, so a 0-byte 404
# body looked identical to a real ISO to anything that only checked for the
# marker's existence.
#
# This script writes the marker ONLY after every artifact is confirmed
# non-empty and its SHA256 matches the value published in SHA256SUMS for
# that exact filename. Any failure (HTTP error, 0-byte artifact, missing or
# mismatched checksum) removes the marker and any partial artifact bytes it
# just wrote, then exits non-zero.
set -euo pipefail

VERSION="${1:?usage: fetch_base_iso.sh <version> <dest-dir>}"
DEST="${2:?usage: fetch_base_iso.sh <version> <dest-dir>}"

BASE_URL="${LUMEN_FETCH_BASE_URL:-https://cdimage.ubuntu.com/xubuntu/releases/${VERSION}/release}"
MARKER="$DEST/.xubuntu_download_complete"
LOG="$DEST/xubuntu_download.log"

ISO_NAMES=(
  "xubuntu-${VERSION}-desktop-amd64.iso"
  "xubuntu-${VERSION}-minimal-amd64.iso"
)
ARTIFACTS=("${ISO_NAMES[@]}" "SHA256SUMS")

log() { printf '%s %s\n' "$(date -u)" "$*" | tee -a "$LOG" >&2; }

fail() {
  log "FAIL: $*"
  rm -f "$MARKER"
  exit 1
}

require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"; }
require_cmd wget
require_cmd sha256sum

mkdir -p "$DEST"
rm -f "$MARKER"
: > "$LOG"
log "Starting Xubuntu $VERSION download"

for name in "${ARTIFACTS[@]}"; do
  dest_file="$DEST/$name"
  log "fetching $BASE_URL/$name"
  if ! wget -q -O "$dest_file" "$BASE_URL/$name"; then
    rm -f "$dest_file"
    fail "download failed for $name (non-2xx response) -- see $LOG"
  fi
  [ -s "$dest_file" ] || fail "$name downloaded as 0 bytes -- refusing to treat as a valid artifact"
done

sha_file="$DEST/SHA256SUMS"
for iso in "${ISO_NAMES[@]}"; do
  expected="$(grep -E "[ *]${iso}\$" "$sha_file" | awk '{print $1}')"
  [ -n "$expected" ] || fail "$iso has no entry in SHA256SUMS -- refusing to verify blind"
  actual="$(sha256sum "$DEST/$iso" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || fail "$iso checksum mismatch: got $actual, expected $expected"
  log "$iso verified: sha256 $actual"
done

log "Xubuntu $VERSION download verified complete"
touch "$MARKER"
