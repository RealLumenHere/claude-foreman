#!/usr/bin/env bash
# wq#3391 [re-tool] install RetDec (retargetable machine-code decompiler,
# avast/retdec). Upstream ships no apt package for this distro's release, so
# this installs the official prebuilt Linux release tarball rather than
# building ~180MB of LLVM-based C++ from source. Covers ARM/MIPS/x86 per the
# ticket: retdec-decompiler --arch accepts mips/pic32/arm/thumb/arm64/
# powerpc/x86/x86-64 (verified below against the live --help text, not just
# asserted — wq#3228 lesson on checking what a binary actually implements).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

VERSION=5.0
DEST="/home/lumen/bin/retdec-$VERSION"
SYMLINK="/home/lumen/bin/retdec-decompiler"
URL="https://github.com/avast/retdec/releases/download/v$VERSION/RetDec-v$VERSION-Linux-Release.tar.xz"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any
# install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if [ ! -L "$SYMLINK" ]; then
    _vmiss "absent or not a symlink: $SYMLINK"
  elif [ "$(readlink -f "$SYMLINK")" != "$DEST/bin/retdec-decompiler" ]; then
    _vmiss "$SYMLINK does not resolve to $DEST/bin/retdec-decompiler"
  fi
  vflag "$SYMLINK" "mips"
  vflag "$SYMLINK" "arm"
  vflag "$SYMLINK" "x86"
  # wq#1724: a citation into this install.sh only proves the script landed on
  # the branch, not that it ever reached the target — ship a manifest file
  # and copy it onto the live install dir so evidence.json has a deploy_target
  # that byte-matches a file this bundle actually ships.
  vfile "$SRC/MANIFEST.txt" "$DEST/MANIFEST.txt"
  vdone
fi

if [ "$(readlink -f "$SYMLINK" 2>/dev/null || true)" = "$DEST/bin/retdec-decompiler" ] \
   && [ -x "$DEST/bin/retdec-decompiler" ]; then
  echo "retdec $VERSION already installed at $DEST, skipping download"
else
  TARBALL="$(mktemp -t retdec-release-XXXXXX.tar.xz)"
  trap 'rm -f "$TARBALL"' EXIT
  curl -L --fail -o "$TARBALL" "$URL"
  mkdir -p "$DEST"
  tar -xf "$TARBALL" -C "$DEST"
  ln -sf "$DEST/bin/retdec-decompiler" "$SYMLINK"
fi
install -m 644 "$SRC/MANIFEST.txt" "$DEST/MANIFEST.txt"

"$SYMLINK" --version
echo "retdec-decompiler installed: $SYMLINK -> $(readlink -f "$SYMLINK")"
