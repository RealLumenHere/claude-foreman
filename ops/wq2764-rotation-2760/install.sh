#!/usr/bin/env bash
# wq#2764 rotation-2760 settle deploy check (wq#1696 contract, added by the wq#2778 verify).
#
# Nothing to copy: named_values.py is the fixture itself, and it is live the moment the
# primary tree holds it. --verify runs it (every named value still hashes to its target,
# the file still masks clean) and re-runs mask_replay.py 2760, checking it still settles
# at 0 unaccounted — the actual liveness claim behind "nothing rotated".
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE="$SRC/named_values.py"
REPLAY=/home/lumen/foreman/harness/mask_replay.py

# Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if [ ! -r "$LIVE" ]; then
    _vmiss "absent: $LIVE"
  else
    out="$(PYTHONDONTWRITEBYTECODE=1 python3 "$LIVE" 2>&1 || true)"
    case "$out" in
      *MISMATCH*) _vmiss "named_values.py hash mismatch: $out" ;;
    esac
    case "$out" in
      *"self-mask clean"*) ;;
      *) _vmiss "named_values.py no longer masks clean: $out" ;;
    esac
  fi
  if [ ! -r "$REPLAY" ]; then
    _vmiss "absent: $REPLAY"
  else
    replay="$(PYTHONDONTWRITEBYTECODE=1 python3 "$REPLAY" 2760 2>&1 || true)"
    case "$replay" in
      *", 0 unaccounted"*) ;;
      *) _vmiss "mask_replay.py 2760 no longer settles at 0 unaccounted" ;;
    esac
  fi
  vdone
fi

echo "nothing to install: ops/wq2764-rotation-2760/named_values.py ships with the foreman repo."
echo "verify: bash $SRC/install.sh --verify"
echo "tests:  python3 $SRC/named_values.py"
