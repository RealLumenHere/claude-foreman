#!/bin/bash
set -euo pipefail
SOCKET="${REMOTE_CLI_SOCKET_PATH:-/run/remote-cli/shared.sock}"
test -S "$SOCKET" || { echo "FAIL: remote-cli socket not found at $SOCKET"; exit 1; }
echo "OK: remote-cli socket present at $SOCKET"
