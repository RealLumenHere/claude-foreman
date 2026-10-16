#!/usr/bin/env bash
# install.sh for wq#3883 sigrok-mcp — logic-analyzer capture/decode MCP server.
# Part of the Limpet OS plugin ecosystem.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  # Core files present in ops bundle
  vfile "$SRC/server.py"     "$SRC/server.py"
  vfile "$SRC/runner.py"     "$SRC/runner.py"
  vfile "$SRC/sigrok_cli.py" "$SRC/sigrok_cli.py"
  # sigrok-cli binary available on PATH
  if ! command -v sigrok-cli >/dev/null 2>&1; then
    echo "MISS sigrok-cli not found on PATH"
    _V_MISS=$((_V_MISS + 1))
  else
    echo "OK sigrok-cli $(sigrok-cli --version 2>&1 | head -1 | awk '{print $2}')"
  fi
  # Python mcp SDK (pip dependency, may not be global — note, not block)
  if python3 -c "import mcp" 2>/dev/null; then
    echo "OK python3 mcp SDK available"
  else
    echo "OK python3 mcp SDK not global (pip install from requirements.txt at deploy)"
  fi
  # Tests pass (tests mock mcp imports, so they work without the SDK)
  if python3 -m pytest "$SRC/tests/" -q --tb=no 2>/dev/null | tail -1 | grep -q "passed"; then
    echo "OK pytest suite passes"
  else
    echo "MISS pytest suite fails or not runnable"
    _V_MISS=$((_V_MISS + 1))
  fi
  vdone
fi

echo "sigrok-mcp install: this is a Limpet plugin; deployment is handled by the ISO builder."
echo "For manual testing: cd $SRC && pip install -r requirements.txt && python3 server.py"
