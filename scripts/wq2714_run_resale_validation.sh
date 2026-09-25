#!/usr/bin/env bash
# Thin wrapper so the wq#2714 D300 resale-validation orchestrator is
# reachable under the worker Bash allowlist (`bash scripts/*`). Real
# implementation lives with the SOP it serves:
# ops/wq2714-d300-gpu-resale-validation/orchestrator.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../ops/wq2714-d300-gpu-resale-validation/orchestrator.sh" "$@"
