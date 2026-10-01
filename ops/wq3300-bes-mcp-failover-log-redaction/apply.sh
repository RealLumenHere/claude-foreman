#!/usr/bin/env bash
# wq#3300 apply (part 1 of 2) — stop bes-mcp-failover's MCP body/chatter stream
# from reaching journald. Port of wq#1698 (ops/wq1698-supergateway-log-redaction/)
# to Bes. Run FROM LUMEN — this worker's own sandbox cannot ssh to Bes or run
# sudo, so this is a proposal for an operator/session with that access
# (same posture as wq1698/apply.sh and wq3092/apply.sh, which this mirrors).
# Never restarts bes-mcp-failover more than once. Never reboots.
set -euo pipefail
BES=bes@100.110.156.91
OPS=/home/lumen/foreman/ops/wq3300-bes-mcp-failover-log-redaction
DROPIN_DIR=/etc/systemd/system/bes-mcp-failover.service.d
LOGDIR=/home/bes/logs/supergateway
LOGFILE="$LOGDIR/bes-mcp-failover.log"
b() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$BES" "$@"; }
fail() { echo "CHECK FAILED: $*"; exit 1; }

# Quiet-window guard, same rationale as wq#1698/wq#829: restarting the
# gateway mid-request knocks over whatever chat/session is in flight.
recent=$(b "journalctl -u bes-mcp-failover --since '10 min ago' --no-pager" | grep -c "StreamableHttp → Child" || true)
if [ "${recent:-0}" -gt 0 ]; then
  echo "REFUSING: $recent gateway requests in the last 10 min — work is in flight."
  echo "Re-run when chats are quiet, or override with WQ3300_FORCE=1."
  [ "${WQ3300_FORCE:-0}" = 1 ] || exit 1
fi

b "sudo -n install -d -o bes -g bes -m 0700 $LOGDIR"
b "[ -e $LOGFILE ] || sudo -n install -o bes -g bes -m 0600 /dev/null $LOGFILE"
b "sudo -n chmod 0600 $LOGFILE"

scp -o BatchMode=yes "$OPS/no-body-logs-journald.conf" "$BES:/tmp/no-body-logs-journald.conf" || fail "scp drop-in failed"
scp -o BatchMode=yes "$OPS/bes-mcp-failover-logrotate.conf" "$BES:/tmp/bes-mcp-failover-logrotate.conf" || fail "scp logrotate failed"
b "sudo -n install -D -m 0644 /tmp/no-body-logs-journald.conf $DROPIN_DIR/no-body-logs-journald.conf" || fail "install drop-in failed"
b "sudo -n install -D -m 0644 /tmp/bes-mcp-failover-logrotate.conf /etc/logrotate.d/bes-mcp-failover" || fail "install logrotate failed"

b "sudo -n systemctl daemon-reload" || fail "daemon-reload failed"
# Detached restart (instinct: this gateway's path changes only via detached
# systemd-run, never a foreground call from this session).
b "sudo -n systemd-run --collect --unit=wq3300-restart-mcp-failover systemctl restart bes-mcp-failover" || fail "restart dispatch failed"
for i in $(seq 1 15); do
  sleep 1
  b "systemctl is-active --quiet bes-mcp-failover" && break
done
b "systemctl is-active --quiet bes-mcp-failover" || fail "bes-mcp-failover not active after restart"

sleep 2
hc=$(b "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health" || echo 000)
echo "post-apply health=$hc"
[ "$hc" = 200 ] || fail "HEALTH CHECK FAILED — roll back: rm $DROPIN_DIR/no-body-logs-journald.conf, daemon-reload, restart"

echo "APPLIED. Body/chatter now at $LOGFILE (0600, logrotate-capped). journald keeps error-level only."
echo "Next: run apply-mask.sh (wq#3300 part 2) to mask secrets in that file too, then journalctl --vacuum-size on Bes, then rotate the fleet sudo password."
