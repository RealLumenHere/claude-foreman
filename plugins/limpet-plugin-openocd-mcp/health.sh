#!/bin/bash
# Health check: verify openocd-mcp is importable and OpenOCD is present
set -euo pipefail
python3 -c "import openocd_mcp" 2>/dev/null || { echo "FAIL: openocd_mcp not importable"; exit 1; }
command -v openocd &>/dev/null || { echo "FAIL: openocd not found"; exit 1; }
echo "OK: openocd-mcp healthy"
