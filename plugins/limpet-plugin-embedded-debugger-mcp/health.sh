#!/bin/bash
# Health check: verify embedded-debugger-mcp is on PATH and reports itself
set -euo pipefail
command -v embedded-debugger-mcp &>/dev/null || { echo "FAIL: embedded-debugger-mcp not found"; exit 1; }
embedded-debugger-mcp --help 2>&1 | grep -q 'embedded-debugger-mcp' || { echo "FAIL: embedded-debugger-mcp --help did not report itself"; exit 1; }
echo "OK: embedded-debugger-mcp healthy"
