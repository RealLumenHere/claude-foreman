#!/usr/bin/env bash
# wq#3967 LIMPET OFRAK plugin. Like ops/wq3882-limpet-flashrom-mcp-a1's
# install.sh, this bundle has no live deploy step yet: the cli_tools entries
# are documented in README.md "Registering as a meta-server child" and
# already present in ops/wq3808-limpet-mcp-meta-server-a1/manifest.example.json
# with enabled=false, but flipping them to enabled=true is a separate,
# Frank-reviewed change -- not because of code risk, but because OFRAK's
# Community License excludes the Shared Use the meta-server's whole purpose
# is (see README.md "Licensing"). This script only verifies the bundle and
# prints the real `ofrak` install step, which needs a 3.11/3.12 interpreter
# (python3 -m pip install ofrak fails to build against the default Python
# 3.14 here -- its pinned aiohttp Cython extension doesn't compile, see
# ISSUE-LOG.md).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PY312="/home/lumen/.local/bin/python3.12"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if ! python3 -m pytest "$SRC/tests" -q >/dev/null 2>&1; then
    _vmiss "primary-tree test suite fails: ops/wq3967-limpet-ofrak-a1/tests"
  fi
  vdone
fi

echo "wq3967-limpet-ofrak-a1 has no live install step: the cli_tools entries" >&2
echo "in ops/wq3808-limpet-mcp-meta-server-a1/manifest.example.json are enabled=false" >&2
echo "pending an OFRAK Pro/Enterprise license purchase (README.md 'Licensing')." >&2
echo "" >&2
echo "To provision a working 'ofrak' install for real use (e.g. to flip the" >&2
echo "selftest.py health check from failing to passing), run on a 3.11/3.12" >&2
echo "interpreter -- NOT the default python3 here, which is 3.14 and cannot" >&2
echo "build ofrak's pinned aiohttp:" >&2
echo "  $PY312 -m venv $SRC/.venv" >&2
echo "  $SRC/.venv/bin/pip install -r $SRC/requirements.txt" >&2
echo "" >&2
echo "verify: bash $SRC/install.sh --verify" >&2
echo "tests:  python3 -m pytest $SRC/tests -q" >&2
exit 0
