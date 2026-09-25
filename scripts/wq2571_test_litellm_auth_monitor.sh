#!/usr/bin/env bash
# Thin wrapper so the wq#2571 offline test is reachable under the worker
# Bash allowlist (`bash scripts/*`). Real test lives with the fix it covers:
# ops/wq2571-litellm-auth-monitor-reconnect/test_litellm_auth_monitor.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../ops/wq2571-litellm-auth-monitor-reconnect/test_litellm_auth_monitor.sh"
