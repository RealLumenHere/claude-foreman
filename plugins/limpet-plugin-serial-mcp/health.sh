#!/bin/bash
# Health check: verify serial-mcp is installed and importable
set -euo pipefail
VENV="/opt/limpet/plugins/serial-mcp/venv"
[ -x "$VENV/bin/python3" ] || { echo "FAIL: serial-mcp venv not found"; exit 1; }
"$VENV/bin/python3" -c 'import serial_mcp' &>/dev/null || { echo "FAIL: serial_mcp not importable"; exit 1; }
echo "OK: serial-mcp healthy"
