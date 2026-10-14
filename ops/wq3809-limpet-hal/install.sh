#!/usr/bin/env bash
# wq#3809 LIMPET hardware abstraction layer deploy check (wq#1696 contract, added by
# the wq#3814 verify — wq3809-limpet-hal shipped with no --verify handler, which
# ops/VERIFY_CONTRACT.md's "repo-only check does not count" rule (wq#1696) does not
# accept on its own).
#
# Nothing to copy: hal.py is live the moment the primary tree holds it at this same
# ops/ path. --verify confirms the primary tree's copy still implements the CLI
# surface and plugin registry this bundle documents, so a later edit that silently
# drops a subcommand or a plugin class is caught instead of only failing at call time.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE="/home/lumen/foreman/ops/wq3809-limpet-hal/hal.py"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$LIVE" '^REGISTRY: tuple\[HardwarePlugin, \.\.\.\] = \('
  vcontains "$LIVE" 'sub\.add_parser\("detect"'
  vcontains "$LIVE" 'sub\.add_parser\("list-plugins"'
  if [ -r "$LIVE" ]; then
    out="$(PYTHONDONTWRITEBYTECODE=1 python3 "$LIVE" list-plugins 2>&1)" || {
      _vmiss "list-plugins did not run cleanly: $out"
      out=""
    }
    if [ -n "$out" ]; then
      count="$(printf '%s' "$out" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo ERR)"
      case "$count" in
        8) ;;
        *) _vmiss "list-plugins reported '$count' plugins, expected 8" ;;
      esac
    fi
  fi
  vdone
fi

echo "nothing to install: ops/wq3809-limpet-hal/hal.py ships with the foreman repo."
echo "verify: bash $SRC/install.sh --verify"
echo "tests:  python3 -m pytest $SRC/tests/test_hal.py -q"
