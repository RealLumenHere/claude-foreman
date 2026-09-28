#!/bin/bash
# wq#2856 — install install-script into /home/lumen/bin, using itself (by rename).
# Idempotent: prints "unchanged:" when the live copy already matches.
# --dry-run: report what would change, touch nothing.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DST=${INSTALL_SCRIPT_DST:-/home/lumen/bin/install-script}

if [ "${1:-}" = "--dry-run" ]; then
  if [ -e "$DST" ] && cmp -s "$SRC/install-script" "$DST"; then
    echo "dry-run: $DST already current"
  else
    echo "dry-run: would install $SRC/install-script -> $DST"
  fi
  exit 0
fi

"$SRC/install-script" -m 0755 "$SRC/install-script" "$DST"
"$DST" --help | grep -q '^usage: install-script' || { echo "installed $DST fails --help" >&2; exit 1; }
