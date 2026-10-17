#!/bin/bash
# Health check: verify buspirate-mcp's venv is installed and importable
set -euo pipefail
VENV_PY="/opt/limpet/plugins/buspirate-mcp/venv/bin/python3"
[ -x "$VENV_PY" ] || { echo "FAIL: buspirate-mcp venv not found"; exit 1; }
"$VENV_PY" -c 'import buspirate_mcp' 2>/dev/null || { echo "FAIL: buspirate_mcp not importable"; exit 1; }
echo "OK: buspirate-mcp healthy"
