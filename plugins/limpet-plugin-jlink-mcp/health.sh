#!/bin/bash
# Health check: verify jlink-mcp is installed and runnable
set -euo pipefail
command -v jlink-mcp &>/dev/null || { echo "FAIL: jlink-mcp not found"; exit 1; }
echo "OK: jlink-mcp healthy"
