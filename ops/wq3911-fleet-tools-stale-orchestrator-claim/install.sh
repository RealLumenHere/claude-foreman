#!/usr/bin/env bash
# wq#3911 fleet_tools.yaml stale-orchestrator-claim correction. minerva/ is a primary
# tree this worker's guard hook refuses to Write/Edit (wq#1719/wq#900), so this bundle
# never installs anything itself — the live edit is hand-applied from
# wq3911_fleet_tools.diff per README.md. This handler only verifies the live target
# carries the corrected text, same shape as wq2607-dc-endpoint-anubis/install.sh for
# this identical file.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
CTX=/home/lumen/minerva/context

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$CTX/fleet_tools.yaml" "DORMANT since 2026-07 \(wq#2741/wq#3911"
  vcontains "$CTX/fleet_tools.yaml" "STALE as of wq#2741/wq#3911"
  vdone
fi

echo "wq3911-fleet-tools-stale-orchestrator-claim: nothing to install from this lane —" >&2
echo "minerva/context/fleet_tools.yaml is a primary-tree live edit, hand-applied from" >&2
echo "wq3911_fleet_tools.diff. See README.md for the exact remaining command." >&2
exit 2
