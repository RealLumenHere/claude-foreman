#!/bin/bash
# Test that health.sh returns 0 when gnuradio-mcp is installed
set -euo pipefail
DIR="$(dirname "$(dirname "$0")")"
bash "$DIR/health.sh" && echo "PASS: health check" || { echo "FAIL: health check"; exit 1; }
