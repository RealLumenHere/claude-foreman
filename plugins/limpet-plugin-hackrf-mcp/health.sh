#!/bin/bash
# Health check: verify hackrf-mcp's venv is installed and importable, the TX guard
# proxy script is present, and the hackrf system package is on PATH.
set -euo pipefail
VENV_PY="/opt/limpet/plugins/hackrf-mcp/venv/bin/python3"
GUARD="/opt/limpet/plugins/hackrf-mcp/mcp/hackrf_tx_guard.py"
[ -x "$VENV_PY" ] || { echo "FAIL: hackrf-mcp venv not found"; exit 1; }
"$VENV_PY" -c 'import hackrf_mcp' 2>/dev/null || { echo "FAIL: hackrf_mcp not importable"; exit 1; }
[ -f "$GUARD" ] || { echo "FAIL: hackrf_tx_guard.py TX-refusal proxy not found"; exit 1; }
command -v hackrf_info &>/dev/null || { echo "FAIL: hackrf system package (hackrf_info) not found"; exit 1; }
echo "OK: hackrf-mcp healthy"
