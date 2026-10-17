#!/bin/bash
# Health check: verify mcjtag is importable and OpenOCD is present
set -euo pipefail
python3 -c "import mcjtag" 2>/dev/null || { echo "FAIL: mcjtag not importable"; exit 1; }
command -v openocd &>/dev/null || { echo "FAIL: openocd not found"; exit 1; }
echo "OK: mcjtag healthy"
