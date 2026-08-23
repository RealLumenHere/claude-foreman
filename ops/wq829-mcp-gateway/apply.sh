#!/usr/bin/env bash
# wq#829 rollout — run AS ROOT, only in a quiet window (script enforces both).
# Applies the supergateway admission-gate patch + the fast-stop drop-in, restarts
# the gateway once, health-checks it. Rollback: restore the .bak this script
# writes, remove the drop-in, daemon-reload, restart.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DIST=/home/lumen/.npm-global/lib/node_modules/supergateway/dist/gateways/stdioToStatelessStreamableHttp.js
DROPIN_DIR=/etc/systemd/system/desktop-commander-mcp.service.d

[ "$(id -u)" = 0 ] || { echo "must run as root (unit drop-in + restart)"; exit 1; }

# Quiet-window guard: refuse if the gateway served traffic in the last 10 min.
# Poking the gateway mid-run knocks over every chat that depends on it (wq#829).
recent=$(journalctl -u desktop-commander-mcp --since "10 min ago" --no-pager | grep -c "StreamableHttp → Child" || true)
if [ "${recent:-0}" -gt 0 ]; then
  echo "REFUSING: $recent gateway requests in the last 10 min — work is in flight."
  echo "Re-run when chats are quiet, or override with WQ829_FORCE=1."
  [ "${WQ829_FORCE:-0}" = 1 ] || exit 1
fi

node --check "$HERE/stdioToStatelessStreamableHttp.js"
cp -a "$DIST" "$DIST.bak.wq829.$(date +%Y%m%d_%H%M%S)"
install -o lumen -g lumen -m 0644 "$HERE/stdioToStatelessStreamableHttp.js" "$DIST"
install -D -m 0644 "$HERE/wq829-fast-stop.conf" "$DROPIN_DIR/wq829-fast-stop.conf"
systemctl daemon-reload
systemctl restart desktop-commander-mcp

sleep 3
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health || echo 000)
echo "post-apply health=$hc"
[ "$hc" = 200 ] || { echo "HEALTH CHECK FAILED — roll back: restore $DIST.bak.wq829.*, rm drop-in, daemon-reload, restart"; exit 1; }

# Smoke: one real MCP round-trip through the patched path.
code=$(curl -s -m 30 -o /tmp/wq829_smoke.json -w '%{http_code}' \
  -X POST http://127.0.0.1:3100/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}')
echo "smoke tools/list http=$code (body: /tmp/wq829_smoke.json)"
[ "$code" = 200 ] || exit 1
echo "APPLIED. Now run: $HERE/loadtest.sh   (determine the real ceiling, update the drop-in)"
