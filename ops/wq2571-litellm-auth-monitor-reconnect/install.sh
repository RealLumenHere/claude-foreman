#!/usr/bin/env bash
# Install the wq#2571 litellm-auth-monitor reconnect/backoff fix.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TARGET=/home/lumen/bin/litellm-auth-monitor

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/litellm-auth-monitor" "$TARGET"
  if grep -q 'reconnects automatically' "$TARGET" 2>/dev/null; then
    _vmiss "$TARGET still claims docker logs --follow reconnects automatically (unfixed, wq#2571)"
  fi
  vdone
fi

install -m 0755 "$HERE/litellm-auth-monitor" "$TARGET"
sudo systemctl restart lumen-litellm-auth-monitor.service
sleep 1
systemctl show lumen-litellm-auth-monitor.service -p ActiveState,NRestarts --no-pager
