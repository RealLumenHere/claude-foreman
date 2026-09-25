#!/usr/bin/env bash
# wq#2605 rollout — run AS ROOT. Not runnable from a BUILD worker sandbox
# (ISSUE-LOG #68/#731: privilege-escalation verb is denylisted there) — this is
# the proposal for Frank/an authorized deploy path, same pattern as
# ops/wq1698-supergateway-log-redaction/apply.sh and
# ops/wq2606-mcp-gateway-watchdog-a1/apply.sh.
#
# Installs persistent CPU/IO/OOM priority drop-ins on the control-plane units
# (desktop-commander-mcp, cloudflared), creates lumen-workers.slice for job
# isolation, and restarts both control-plane units via DETACHED systemd-run
# (Commander rides cloudflared, so this session's own connection must survive
# the restart of the thing carrying it). Backs up every unit/drop-in file it
# touches to <path>.pre-ha.bak (only if a backup doesn't already exist, so
# re-running this script is idempotent and never clobbers the ORIGINAL state).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" = 0 ] || { echo "must run as root (unit drop-ins + slice + restart)"; exit 1; }

DC_DROPIN_DIR=/etc/systemd/system/desktop-commander-mcp.service.d
CFD_DROPIN_DIR=/etc/systemd/system/cloudflared.service.d
DC_UNIT=/etc/systemd/system/desktop-commander-mcp.service
CFD_UNIT=/etc/systemd/system/cloudflared.service
SLICE_UNIT=/etc/systemd/system/lumen-workers.slice

backup() {
  # $1 = path to back up. No-op if the path doesn't exist yet, or a backup
  # already exists (first backup wins — it is the pre-wq2605 baseline).
  local path="$1"
  [ -e "$path" ] || return 0
  [ -e "${path}.pre-ha.bak" ] && return 0
  cp -a "$path" "${path}.pre-ha.bak"
  echo "backed up $path -> ${path}.pre-ha.bak"
}

# Same quiet-window guard as ops/wq1698 and ops/wq829 — restarting the gateway
# mid-request knocks over whatever chat/session is in flight.
recent=$(journalctl -u desktop-commander-mcp --since "10 min ago" --no-pager | grep -c "StreamableHttp → Child" || true)
if [ "${recent:-0}" -gt 0 ]; then
  echo "REFUSING: $recent gateway requests in the last 10 min — work is in flight."
  echo "Re-run when chats are quiet, or override with WQ2605_FORCE=1."
  [ "${WQ2605_FORCE:-0}" = 1 ] || exit 1
fi

backup "$DC_UNIT"
backup "$CFD_UNIT"
[ -f "$DC_DROPIN_DIR/10-control-plane-priority.conf" ] && backup "$DC_DROPIN_DIR/10-control-plane-priority.conf"
[ -f "$CFD_DROPIN_DIR/10-control-plane-priority.conf" ] && backup "$CFD_DROPIN_DIR/10-control-plane-priority.conf"
[ -f "$SLICE_UNIT" ] && backup "$SLICE_UNIT"

install -D -m 0644 "$HERE/desktop-commander-mcp.conf" "$DC_DROPIN_DIR/10-control-plane-priority.conf"
install -D -m 0644 "$HERE/cloudflared.conf" "$CFD_DROPIN_DIR/10-control-plane-priority.conf"
install -m 0644 "$HERE/lumen-workers.slice" "$SLICE_UNIT"

systemctl daemon-reload

# Detached restarts (instinct 5: Vault/cloudflared/Commander path changes only
# via detached systemd-run, never foreground — this session's own MCP
# connection rides cloudflared, so a foreground restart of it would cut this
# script off mid-run).
systemd-run --collect --unit=wq2605-restart-commander systemctl restart desktop-commander-mcp
systemd-run --collect --unit=wq2605-restart-cloudflared systemctl restart cloudflared

for i in $(seq 1 15); do
  sleep 1
  systemctl is-active --quiet desktop-commander-mcp && systemctl is-active --quiet cloudflared && break
done

sleep 2
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health || echo 000)
echo "post-apply health=$hc"
[ "$hc" = 200 ] || {
  echo "HEALTH CHECK FAILED — roll back with $HERE/rollback.sh"
  exit 1
}

echo "APPLIED. desktop-commander-mcp + cloudflared: CPUWeight=1000 IOWeight=1000 Nice=-5 OOMScoreAdjust=-900 (persistent)."
echo "lumen-workers.slice created: CPUWeight=50 IOWeight=50 MemoryHigh=40G."
echo "Next: land the bin/lumen-run --slice wiring (this bundle's README) so job/worker spawns —"
echo "including foreman's fworker spawns, which route through lumen-run — land in"
echo "lumen-workers.slice, then run stress_test.sh."
