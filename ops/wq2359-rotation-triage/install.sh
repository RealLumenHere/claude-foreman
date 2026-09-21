#!/usr/bin/env bash
# wq#2359 rotation-triage deploy check (wq#1696 contract, added by the wq#2410 verify).
#
# Nothing to copy: the tool is harness/mask_triage.py in the foreman repo, and it is
# live the moment the primary tree holds it. --verify says whether it does, and whether
# the live copy still answers the batch and history flags this bundle shipped.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/foreman/harness

# Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$LIVE/mask_triage.py" '^def history_text\('
  vcontains "$LIVE/mask_triage.py" '^def batch_line\('
  vcontains "$LIVE/mask_triage.py" '"--no-history"'
  vcontains "$LIVE/mask_triage.py" 'rev-list.*--all.*--objects'
  vcontains "$LIVE/mask_replay.py" '^def tracked_text\('
  if [ -r "$LIVE/mask_triage.py" ]; then
    help="$(PYTHONDONTWRITEBYTECODE=1 python3 "$LIVE/mask_triage.py" --help 2>&1 || true)"
    case "$help" in
      *--no-history*) ;;
      *) _vmiss "mask_triage.py --help does not list --no-history" ;;
    esac
  fi
  vdone
fi

echo "nothing to install: harness/mask_triage.py ships with the foreman repo."
echo "verify: bash $SRC/install.sh --verify"
echo "tests:  python3 -m pytest /home/lumen/foreman/harness/tests/test_mask_triage.py -q"
