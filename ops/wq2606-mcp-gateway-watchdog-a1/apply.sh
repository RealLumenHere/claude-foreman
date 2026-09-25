#!/usr/bin/env bash
# wq#2606 rollout — run AS ROOT on the target node.
# Installs the watchdog script + timer, the cloudflared --metrics drop-in,
# and the broadened supergateway logrotate config; enables the timer;
# restarts cloudflared once (detached, health-checked) to pick up --metrics.
# Does NOT touch desktop-commander-mcp/dc-gateway — the watchdog's own probe
# is what restarts that unit going forward, no reason to bounce it here.
# Rollback: systemctl disable --now mcp-gateway-watchdog.timer, rm the
# installed files below, remove the cloudflared drop-in + daemon-reload +
# restart cloudflared to drop --metrics.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# Node-specific: Anubis/Bes/Nephthys use dc-gateway instead of
# desktop-commander-mcp (MCP_CONTROL_PLANE_HA_DESIGN.md §3.2). Override via
# env before running this script on those nodes.
GATEWAY_UNIT=${WQ2606_GATEWAY_UNIT:-desktop-commander-mcp}
GATEWAY_PORT=${WQ2606_GATEWAY_PORT:-3100}

[ "$(id -u)" = 0 ] || { echo "must run as root (unit install + daemon-reload + restart)"; exit 1; }

install -D -m 0755 "$HERE/mcp-gateway-watchdog.sh" /home/lumen/bin/mcp-gateway-watchdog.sh
chown lumen:lumen /home/lumen/bin/mcp-gateway-watchdog.sh

sed "s#--unit desktop-commander-mcp --port 3100#--unit ${GATEWAY_UNIT} --port ${GATEWAY_PORT}#" \
  "$HERE/mcp-gateway-watchdog.service" > /etc/systemd/system/mcp-gateway-watchdog.service
install -D -m 0644 "$HERE/mcp-gateway-watchdog.timer" /etc/systemd/system/mcp-gateway-watchdog.timer
install -D -m 0644 "$HERE/cloudflared-metrics.conf" /etc/systemd/system/cloudflared.service.d/wq2606-metrics.conf
install -D -m 0644 "$HERE/supergateway-logrotate.conf" /etc/logrotate.d/supergateway

systemctl daemon-reload
systemctl enable --now mcp-gateway-watchdog.timer

# Quiet-window guard before bouncing cloudflared, same rationale as wq#829/wq#1698:
# a mid-request restart knocks over whatever tunnel connections are in flight.
recent=$(journalctl -u cloudflared --since "5 min ago" --no-pager | grep -c "" || true)
if [ "${recent:-0}" -gt 200 ]; then
  echo "REFUSING cloudflared restart: $recent journal lines in the last 5 min — looks busy."
  echo "Re-run when quiet, or override with WQ2606_FORCE=1."
  [ "${WQ2606_FORCE:-0}" = 1 ] || exit 1
fi

systemd-run --collect --unit=wq2606-restart-cloudflared systemctl restart cloudflared
for i in $(seq 1 15); do
  sleep 1
  systemctl is-active --quiet cloudflared && break
done

sleep 2
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:20241/ready || echo 000)
echo "cloudflared --metrics post-restart /ready=$hc"
[ "$hc" = 200 ] || echo "WARNING: /ready not 200 yet -- give it a few seconds, or check 'systemctl status cloudflared'"

echo "APPLIED. mcp-gateway-watchdog.timer active (1 min). Gateway unit=${GATEWAY_UNIT} port=${GATEWAY_PORT}."
echo "mcp-pressure-guard.timer is untouched and keeps running (memory branch)."
