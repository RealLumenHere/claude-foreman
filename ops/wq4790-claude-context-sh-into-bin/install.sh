#!/bin/bash
# wq#4790 — deploy the tracked claude_context.sh over /home/lumen/bin/claude_context.sh,
# replacing the exec shim that used to point at the untracked minerva/context copy.
# Idempotent: prints "unchanged:" when the live copy already matches.
# --dry-run: report what would change, touch nothing.
#
# Atomic rename, not an in-place overwrite: /home/lumen/bin/claude_context.sh is a live
# script other sessions may be mid-exec of when this runs, and overwriting an inode a
# running process still has open changes what it reads from under it (wq#2856 — see
# guard.sh's PRIMARY_TREES wall, which denies a direct cp/edit here for the same reason
# and prescribes this cp-to-tmp-then-mv shape as the sanctioned remedy).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DST=${CLAUDE_CONTEXT_DST:-/home/lumen/bin/claude_context.sh}

if [ "${1:-}" = "--dry-run" ]; then
  if [ -e "$DST" ] && cmp -s "$SRC/claude_context.sh" "$DST"; then
    echo "dry-run: $DST already current"
  else
    echo "dry-run: would install $SRC/claude_context.sh -> $DST"
  fi
  exit 0
fi

if [ -e "$DST" ] && cmp -s "$SRC/claude_context.sh" "$DST"; then
  echo "unchanged: $DST"
  exit 0
fi

TMP="$DST.tmp.$$"
cp "$SRC/claude_context.sh" "$TMP"
chmod 0755 "$TMP"
mv -f "$TMP" "$DST"
echo "installed $SRC/claude_context.sh -> $DST"

echo "verify before/after with: diff <(bash $DST --core 2>&1) <(bash /home/lumen/minerva/context/claude_context.sh --core 2>&1)"
