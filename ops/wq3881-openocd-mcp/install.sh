#!/usr/bin/env bash
# wq#3881 openocd-mcp: no deploy step exists yet. server.py is not wired into any
# systemd unit, HAL role, or other bundle's invocation anywhere on the fleet — it
# isn't even listed (enabled or disabled) in ops/wq3808-limpet-mcp-meta-server-a1's
# manifest.example.json alongside the ghidra/radare2 placeholder entries it sits next
# to in server.py's own docstring (wq#3892 verify-pass). There is nothing installed
# to byte-match or any unit to check is-active.
#
# The only live signal available right now is that the bundle's own code actually
# behaves as shipped when run from the primary tree: a bare "does this file exist in
# git" check would be exactly the repo-only check ops/VERIFY_CONTRACT.md (wq#1696)
# rules out — it cannot fail from anything that actually breaks the client. Running
# its test suite can: tests/test_openocd_client.py spawns tests/fake_openocd.py as a
# real subprocess speaking the real Tcl RPC wire protocol over a real loopback
# socket, so connect()/disconnect(), every command's wire format, and the
# invalid-input rejections (bad width, bad reset mode, not-yet-connected, empty
# write, missing flash image, non-positive scan length) all show up as a real
# failure here if the client regresses. server.py's FastMCP wrapper itself stays
# unexercised by this check: the `mcp` package isn't installed in this sandbox
# (no pip/pip3 entry in harness/worker-settings.json, same constraint
# ops/wq1151-lumen-kb/requirements.txt documents for its own server.py, logged at
# kb/ISSUE-LOG.md row 1309) — but it is a one-line-per-tool pass-through with no
# logic of its own to diverge from openocd_client.py's tested behavior.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if ! python3 -I -m pytest "$SRC/tests" -q >/dev/null 2>&1; then
    _vmiss "primary-tree test suite fails: ops/wq3881-openocd-mcp/tests"
  fi
  vdone
fi

echo "wq3881-openocd-mcp has no install step yet: server.py is not wired into any" >&2
echo "systemd unit, HAL role, or caller on the fleet. Registering it as a real" >&2
echo "child server (e.g. in wq3808's meta-server manifest) is a separate change." >&2
echo "verify: bash $SRC/install.sh --verify" >&2
echo "tests:  python3 -I -m pytest $SRC/tests -q" >&2
exit 0
