#!/bin/bash
# Health check: verify gnuradio-mcp is installed and runnable
set -euo pipefail
command -v gnuradio-mcp &>/dev/null || python3 -c 'import gnuradio_mcp' 2>/dev/null || { echo "FAIL: gnuradio-mcp not found"; exit 1; }
echo "OK: gnuradio-mcp healthy"
