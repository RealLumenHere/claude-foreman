#!/usr/bin/env bash
# wq#2609 — re-enable nephthys-mcp-failover.service + cloudflared on Nephthys
# itself, pin the npm packages behind them, and wire the wq#2606 T2 watchdog.
#
# Both units already exist on Nephthys (they were the live failover before
# wq#589's 2026-08-23 decommission moved the ROLE to Bes — see this
# directory's README "Root cause" section). This is a re-enable of an
# existing pair, not a fresh install: no unit files are written here.
#
# Run AS ROOT on Nephthys (ssh nephthys@<tailscale-ip>, currently 100.98.74.87
# per lumen_state.py current nephthys tailscale_ip — reconfirm, addresses
# rotate). This worker's sandbox has no ssh/fleet-exec path to Nephthys
# (README "Why this worker could not run it"), so this script is staged,
# not executed, exactly like ops/wq2607-dc-endpoint-anubis/'s remaining
# commands.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

UNIT=nephthys-mcp-failover
PORT=3100
SUPERGATEWAY_VERSION=3.4.3
DESKTOP_COMMANDER_VERSION=0.2.46

[ "$(id -u)" = 0 ] || { echo "must run as root (systemctl enable + npm -g + watchdog install)"; exit 1; }

echo "== pin versions (wq#2609 ask: 3.4.3 / 0.2.46, same pins as wq#2607's Anubis rollout) =="
npm i -g "supergateway@${SUPERGATEWAY_VERSION}" "@wonderwhy-er/desktop-commander@${DESKTOP_COMMANDER_VERSION}"

echo "== re-enable the pair (dead by config since 2026-08-23, per wq#2609) =="
systemctl enable --now "${UNIT}"
systemctl enable --now cloudflared

echo "== local origin checks =="
for i in $(seq 1 15); do
  sleep 1
  systemctl is-active --quiet "${UNIT}" && systemctl is-active --quiet cloudflared && break
done
curl -s -m 5 -o /dev/null -w 'local /health=%{http_code}\n' "http://127.0.0.1:${PORT}/health"
curl -s -m 10 -o /dev/null -w 'local tools/list=%{http_code}\n' \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "http://127.0.0.1:${PORT}/mcp"

echo "== public tunnel checks (Access-gated, service token e23d092b per wq#589) =="
curl -s -o /dev/null -w 'https://mcp-failover.lumennode.uk/mcp no-token=%{http_code} (want 403)\n' \
  https://mcp-failover.lumennode.uk/mcp

echo "== T2 watchdog (reuse wq#2606's node-agnostic script unmodified) =="
WQ2606_GATEWAY_UNIT="${UNIT}" WQ2606_GATEWAY_PORT="${PORT}" \
  "$HERE/../wq2606-mcp-gateway-watchdog-a1/apply.sh"

echo "DONE. Run this directory's verify.sh for the pass/fail summary, then:"
echo "  python3 /home/lumen/minerva/lumen_state.py ingest nephthys mcp_failover enabled --source wq2609"
echo "  python3 /home/lumen/minerva/lumen_state.py regen"
