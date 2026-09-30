#!/usr/bin/env bash
# wq#1698 rollout — run AS ROOT, only in a quiet window (script enforces both).
# Stops supergateway's MCP body/chatter stream from reaching journald (and
# from there, vlogs-forwarder on Bes) by splitting the unit's stdout+stderr:
# error-level diagnostics stay in journald (small, no bodies), the high-volume
# body dump moves to a local 0600 file capped by logrotate. Restarts the
# gateway once, health-checks it. Rollback: remove the drop-in, daemon-reload,
# restart — desktop-commander-mcp.service's own ExecStart is untouched.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DROPIN_DIR=/etc/systemd/system/desktop-commander-mcp.service.d
LOGDIR=/home/lumen/logs/supergateway
LOGFILE="$LOGDIR/desktop-commander-mcp.log"

[ "$(id -u)" = 0 ] || { echo "must run as root (unit drop-in + restart)"; exit 1; }

# Quiet-window guard, same rationale as wq#829's apply.sh: restarting the
# gateway mid-request knocks over whatever chat/session is in flight.
recent=$(journalctl -u desktop-commander-mcp --since "10 min ago" --no-pager | grep -c "StreamableHttp → Child" || true)
if [ "${recent:-0}" -gt 0 ]; then
  echo "REFUSING: $recent gateway requests in the last 10 min — work is in flight."
  echo "Re-run when chats are quiet, or override with WQ1698_FORCE=1."
  [ "${WQ1698_FORCE:-0}" = 1 ] || exit 1
fi

install -d -o lumen -g lumen -m 0700 "$LOGDIR"
[ -e "$LOGFILE" ] || install -o lumen -g lumen -m 0600 /dev/null "$LOGFILE"
chmod 0600 "$LOGFILE"

install -D -m 0644 "$HERE/no-body-logs-journald.conf" "$DROPIN_DIR/no-body-logs-journald.conf"
install -D -m 0644 "$HERE/supergateway-logrotate.conf" /etc/logrotate.d/supergateway

systemctl daemon-reload
# Detached restart (instinct: Commander's path changes only via detached
# systemd-run, never a foreground call from this session).
systemd-run --collect --unit=wq1698-restart-commander systemctl restart desktop-commander-mcp
for i in $(seq 1 15); do
  sleep 1
  systemctl is-active --quiet desktop-commander-mcp && break
done

sleep 2
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health || echo 000)
echo "post-apply health=$hc"
[ "$hc" = 200 ] || { echo "HEALTH CHECK FAILED — roll back: rm $DROPIN_DIR/no-body-logs-journald.conf, daemon-reload, restart"; exit 1; }

echo "APPLIED. Body/chatter now at $LOGFILE (0600, logrotate-capped). journald keeps error-level only."
echo "Next: run check-bes-secrets.sh (one-time) to check for token-shaped strings already shipped to Bes."
