#!/usr/bin/env bash
# p1-ha-drill.sh -- Commander HA failover drill (wq#2781). RUN LIVE, WITH
# FRANK WATCHING, ONE STEP AT A TIME. Never scheduled, never detached, never
# run as part of the cutover itself (mcpw-cutover-plan.md must already be
# accepted and stable). This is a menu, not a script that runs end to end
# unattended -- each step pauses for a keypress so Frank can inspect state
# before the next one fires.
#
# Steps (from wq#2781's own ticket text): primary DC stop, supergateway
# SIGSTOP, cloudflared stop on active, P1 primary power cut -> backup
# serves; failback. Health = full MCP initialize through mcpw (mcpw-diag.py),
# never process-up. Records flip/failback latency to
# reports/mcp_ha_drill_<date>.md and (from Lumen, after the drill)
# lumen_episode.py commit.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
MCPW_URL=${MCPW_URL:-https://mcpw.lumennode.uk}
REPORT="reports/mcp_ha_drill_$(date -u +%Y%m%d_%H%M%S).md"
mkdir -p reports

log() { echo "$(date -u +%H:%M:%S)  $*" | tee -a "$REPORT"; }
pause() { read -r -p ">>> $1 [enter to continue] "; }

check_active() {
  # Cheap poll: /pool if you have a bearer token, else just time an initialize.
  local start end
  start=$(date +%s.%N)
  if python3 "$HERE/mcpw-diag.py" --base-url "$MCPW_URL" --save-token /tmp/p1-drill-token 2>/dev/null; then
    end=$(date +%s.%N)
    log "initialize OK, $(echo "$end - $start" | bc)s"
    return 0
  else
    log "initialize FAILED"
    return 1
  fi
}

echo "=== P1 Commander HA drill (wq#2781) -- $(date -u) ===" | tee "$REPORT"
echo "MCPW_URL=$MCPW_URL" | tee -a "$REPORT"
pause "Confirm: cutover (mcpw-cutover-plan.md) is deployed and stable, Frank is watching, this is NOT unattended."

echo
echo "1) primary DC stop"
pause "Run on p1-primary:  sudo systemctl stop dc-gateway   -- then press enter"
log "T0: primary dc-gateway stopped (manual step)"
check_active || true
pause "Failback: on p1-primary, sudo systemctl start dc-gateway. Confirm active flips back, then enter."
check_active || true
log "primary DC-stop test done"

echo
echo "2) supergateway SIGSTOP (hung, port open -- the class of bug wq#2606 exists for)"
pause "On p1-primary: pkill -STOP -f 'supergateway.*3100'   -- then press enter"
log "T0: supergateway SIGSTOP'd on primary"
check_active || true
log "watchdog should restart it within ~2min (2 consecutive fails + cooldown) -- wait and observe journalctl -t mcp-gateway-watchdog on p1-primary"
pause "Once the watchdog auto-restarts it (or if it doesn't after 3min, SIGCONT it by hand: pkill -CONT -f 'supergateway.*3100'), press enter"
check_active || true
log "supergateway SIGSTOP test done"

echo
echo "3) cloudflared stop on the currently-ACTIVE node"
pause "Identify the active node via GET $MCPW_URL/pool, then: sudo systemctl stop cloudflared on THAT node. Press enter once stopped."
log "T0: cloudflared stopped on active node"
check_active || true
pause "Failback: sudo systemctl start cloudflared on that node. Confirm recovery, then enter."
check_active || true
log "cloudflared-stop test done"

echo
echo "4) P1 primary power cut (real outage, not a systemctl stop)"
pause "Physically cut power to p1-primary now (Tapo plug / unplug). Press enter once done."
log "T0: p1-primary power cut"
check_active || true
log "expect: active=p1-backup within ~60s of the cron probe noticing (up to 2 consecutive fail probes at 1-min cadence -- worst case ~2min, not instant)"
pause "Confirm backup is serving (check_active above passed against backup). Press enter to restore power."
log "T_power_on: p1-primary powered back on"
pause "Wait for p1-primary to boot + dc-gateway/cloudflared to come up + 2 consecutive healthy probes (failback hysteresis in pool.ts). Press enter once /pool shows active=p1-primary again."
check_active || true
log "power-cut + failback test done"

echo
echo "=== Drill complete. Report: $REPORT ==="
echo "Now, FROM LUMEN, record the episode:"
echo "  python3 /home/lumen/minerva/lumen_episode.py commit --summary 'wq2781 P1 HA drill' --changed 'none (drill only)' --worked '<fill in>' --failed '<fill in>' --why '<fill in>' --kind drill --tags p1,ha,wq2744,wq2781 --ref wq2781"
echo "Fill in real flip/failback latencies from $REPORT into the ticket before closing wq#2781."
