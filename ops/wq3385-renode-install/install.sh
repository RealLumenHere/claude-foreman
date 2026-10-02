#!/usr/bin/env bash
# wq#3385 — install Renode (full MCU+peripheral system emulator, for ESP32/Coral/
# embedded RE work) as a no-root portable build. apt has no `renode` package on
# this box's distro and a BUILD worker has no root anyway (ISSUE-LOG #1153), so
# this installs the upstream portable .NET 8 self-contained tarball instead: it
# embeds its own runtime, needs no apt/root, and just has to land somewhere on
# the box plus a launcher in /home/lumen/bin.
#
# /home/lumen/bin is a PRIMARY tree (wq#900): a worker may drop fresh files into
# it over plain Bash (cp/mv), but the Write/Edit tool and any git branch/commit
# verb targeting it are denied. So the live install already happened by hand the
# same way this script does it below, and the exact bytes that landed are
# recorded in wq3385_bin.diff (git diff --cached, never committed — the
# PRIMARY-tree commit guard refuses that too, same as wq#2095's minerva/context
# edit). This script makes that install reproducible/idempotent instead of a
# one-off.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR=/home/lumen/bin
DIST_DIR="$BIN_DIR/renode-dist"
WRAPPER="$BIN_DIR/renode"
VERSION=1.17.0
URL="https://github.com/renode/renode/releases/download/v${VERSION}/renode-${VERSION}.linux-portable.tar.gz"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if [ ! -x "$WRAPPER" ]; then
    _vmiss "absent or not executable: $WRAPPER"
  else
    OUT="$("$WRAPPER" --version 2>&1)" || { echo "ERR $WRAPPER --version failed: $OUT"; _V_ERR=1; }
    case "$OUT" in
      *"Renode v$VERSION"*) ;;
      *) _vmiss "$WRAPPER --version did not report Renode v$VERSION (got: $OUT)" ;;
    esac
  fi
  [ -x "$DIST_DIR/renode" ] || _vmiss "absent or not executable: $DIST_DIR/renode"
  vdone
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -sL -o "$TMP/renode.tar.gz" "$URL"
python3 -c "
import tarfile
with tarfile.open('$TMP/renode.tar.gz', 'r:gz') as tf:
    tf.extractall('$TMP/extracted')
"
EXTRACTED_DIR="$(find "$TMP/extracted" -maxdepth 1 -mindepth 1 -type d | head -1)"

if [ -e "$DIST_DIR" ]; then
  echo "install: $DIST_DIR already present, leaving it in place (remove it first to reinstall)" >&2
else
  mv "$EXTRACTED_DIR" "$DIST_DIR"
fi

cp "$SRC/renode-wrapper.sh" "$TMP/renode.new"
cp "$TMP/renode.new" "$WRAPPER"
chmod +x "$WRAPPER"

"$WRAPPER" --version
echo "installed: $WRAPPER -> $DIST_DIR/renode"
