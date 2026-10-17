#!/bin/bash
set -euo pipefail
command -v flashrom &>/dev/null || { echo "FAIL: flashrom not found"; exit 1; }
flashrom --version &>/dev/null || { echo "FAIL: flashrom --version failed"; exit 1; }
echo "OK: flashrom healthy"
