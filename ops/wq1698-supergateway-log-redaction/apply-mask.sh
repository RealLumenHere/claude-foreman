#!/usr/bin/env bash
# wq#2546 rollout — run AS ROOT, only in a quiet window (script enforces both).
# Installs wq2546-mask-body-log.conf on top of the wq#1698 drop-ins already in
# place: supergateway's body/chatter stream still lands in
# /home/lumen/logs/supergateway/desktop-commander-mcp.log (unchanged path,
# same logrotate cap), but now flows through /home/lumen/bin/mask-secrets
# first, so a credential printed by a Commander tool result never reaches the
# file in the clear. StandardError (error-level diagnostics) is untouched.
# Rollback: remove the drop-in, daemon-reload, restart — same as apply.sh.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DROPIN_DIR=/etc/systemd/system/desktop-commander-mcp.service.d
LOGFILE=/home/lumen/logs/supergateway/desktop-commander-mcp.log

[ "$(id -u)" = 0 ] || { echo "must run as root (unit drop-in + restart)"; exit 1; }
[ -f "$DROPIN_DIR/no-body-logs-journald.conf" ] || {
  echo "REFUSING: wq#1698's no-body-logs-journald.conf is not installed yet — apply.sh first."
  exit 1
}
[ -x /home/lumen/bin/mask-secrets ] || { echo "REFUSING: /home/lumen/bin/mask-secrets missing or not executable"; exit 1; }

# Quiet-window guard, same rationale as wq#1698/wq#829: restarting the gateway
# mid-request knocks over whatever chat/session is in flight.
recent=$(journalctl -u desktop-commander-mcp --since "10 min ago" --no-pager | grep -c "StreamableHttp → Child" || true)
if [ "${recent:-0}" -gt 0 ]; then
  echo "REFUSING: $recent gateway requests in the last 10 min — work is in flight."
  echo "Re-run when chats are quiet, or override with WQ2546_FORCE=1."
  [ "${WQ2546_FORCE:-0}" = 1 ] || exit 1
fi

install -D -m 0644 "$HERE/wq2546-mask-body-log.conf" "$DROPIN_DIR/wq2546-mask-body-log.conf"

systemctl daemon-reload
# Detached restart (instinct: Commander's path changes only via detached
# systemd-run, never a foreground call from this session).
systemd-run --collect --unit=wq2546-restart-commander systemctl restart desktop-commander-mcp
for i in $(seq 1 15); do
  sleep 1
  systemctl is-active --quiet desktop-commander-mcp && break
done

sleep 2
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health || echo 000)
echo "post-apply health=$hc"
[ "$hc" = 200 ] || { echo "HEALTH CHECK FAILED — roll back: rm $DROPIN_DIR/wq2546-mask-body-log.conf, daemon-reload, restart"; exit 1; }

echo "APPLIED. $LOGFILE now receives masked body/chatter only."
echo "Verify with: tail -n 20 $LOGFILE | /home/lumen/bin/mask-secrets  # should show MASKED, never a raw value, on any credential-shaped line"
