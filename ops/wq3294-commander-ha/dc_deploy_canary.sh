#!/bin/bash
# dc_deploy_canary.sh — wq#3294 item D: gate every DC/supergateway upgrade behind a
# scratch-prefix canary so a crashing build dies on a throwaway port, never on the
# live control plane (root cause of the wq#3294 DC crash-loop outages; see 6d25744a).
#
# Usage: dc_deploy_canary.sh <supergateway-version> <desktop-commander-version> [port]
# Exit 0  -> candidate build served MCP initialize + tools/list; safe to upgrade live.
# Exit !0 -> candidate is broken; the upgrade script MUST abort.
#
# Wire-up (follow-up ticket 4): every pool backend's upgrade path runs this first:
#   dc_deploy_canary.sh "$SG_VER" "$DC_VER" || { echo "canary failed, aborting"; exit 1; }
set -euo pipefail

SG_VER="${1:?supergateway version required}"
DC_VER="${2:?desktop-commander version required}"
PORT="${3:-31294}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRATCH="$(mktemp -d /tmp/dc-canary.XXXXXX)"
trap 'kill "$GW_PID" 2>/dev/null || true; rm -rf "$SCRATCH"' EXIT

echo "canary: installing supergateway@$SG_VER + @wonderwhy-er/desktop-commander@$DC_VER into $SCRATCH"
npm install --prefix "$SCRATCH" --no-fund --no-audit --loglevel=error \
  "supergateway@$SG_VER" "@wonderwhy-er/desktop-commander@$DC_VER"

echo "canary: starting candidate on :$PORT (stateless streamable-HTTP, same shape as live)"
"$SCRATCH/node_modules/.bin/supergateway" \
  --stdio "$SCRATCH/node_modules/.bin/desktop-commander" \
  --outputTransport streamableHttp --port "$PORT" \
  >"$SCRATCH/gateway.log" 2>&1 &
GW_PID=$!

# Give the gateway up to 30s to bind, then run the real e2e. A gateway that binds
# but whose DC child crash-loops fails mcp_probe, which is the point.
for i in $(seq 1 30); do
  if curl -s -o /dev/null -m 2 "http://localhost:$PORT/mcp"; then break; fi
  if ! kill -0 "$GW_PID" 2>/dev/null; then
    echo "canary: FAIL — gateway exited during startup; log tail:" >&2
    tail -20 "$SCRATCH/gateway.log" >&2
    exit 1
  fi
  sleep 1
done

if python3 "$HERE/mcp_probe.py" "http://localhost:$PORT/mcp" 20; then
  echo "canary: PASS — supergateway@$SG_VER + DC@$DC_VER serve MCP; safe to deploy"
  exit 0
else
  echo "canary: FAIL — candidate build does not serve MCP; log tail:" >&2
  tail -20 "$SCRATCH/gateway.log" >&2
  exit 1
fi
