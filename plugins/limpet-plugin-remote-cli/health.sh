#!/bin/bash
set -euo pipefail
SOCKET="/tmp/tmux-$(id -u)/${SHARED_TERMINAL_SOCKET:-shared}"
test -S "$SOCKET" || { echo "FAIL: shared tmux server socket not found at $SOCKET"; exit 1; }
echo "OK: shared tmux server socket present at $SOCKET"
