#!/usr/bin/env bash
# wq#3808 LIMPET unified MCP meta-server: no deploy step exists yet. meta_server.py
# is not wired into any systemd unit, HAL role, or other bundle's invocation anywhere
# on the fleet (grepped limpet/, ops/, fleet-config/ for meta_server.py and
# "limpet-meta-server": no hits outside this bundle's own tree, wq#3815 verify-pass).
# README.md's "Registering a real child server" section confirms this is expected --
# REA is on a SECURITY HOLD (wq#3786) and Ghidra/radare2 MCP have no fleet install, so
# manifest.example.json ships every entry disabled. There is nothing installed to
# byte-match or any unit to check is-active.
#
# The only live signal available right now is that the bundle's own code actually
# behaves as shipped when run from the primary tree: a bare "does this file exist in
# git" check would be exactly the repo-only check ops/VERIFY_CONTRACT.md (wq#1696)
# rules out -- it cannot fail from anything that actually breaks the server. Running
# its test suite can: test_end_to_end_subprocess_speaks_mcp_over_stdio spawns
# meta_server.py as a real subprocess and drives it over stdin/stdout exactly as an
# agent would, so an import error, a broken JSON-RPC response shape, or a regression
# in tool dispatch all show up as a real failure here.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if ! python3 -m pytest "$SRC/tests" -q >/dev/null 2>&1; then
    _vmiss "primary-tree test suite fails: ops/wq3808-limpet-mcp-meta-server-a1/tests"
  fi
  vdone
fi

echo "wq3808-limpet-mcp-meta-server-a1 has no install step yet: meta_server.py is" >&2
echo "not wired into any systemd unit, HAL role, or caller on the fleet. Flipping a" >&2
echo "manifest entry to enabled:true plus a real child-server install (see" >&2
echo "README.md 'Registering a real child server') is a separate change." >&2
echo "verify: bash $SRC/install.sh --verify" >&2
echo "tests:  python3 -m pytest $SRC/tests -q" >&2
exit 0
