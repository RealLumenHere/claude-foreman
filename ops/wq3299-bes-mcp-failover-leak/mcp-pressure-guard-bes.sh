#!/usr/bin/env bash
# mcp-pressure-guard-bes — wq#3299 port of /home/lumen/bin/mcp-pressure-guard.sh
# to bes-mcp-failover.service. Interim hand-reap (2026-10-01 02:5x PDT, 70
# handlers >30min, 12.5G->8.8G) regrows ~2GB/h, so this is the standing
# backstop until the dist swap (apply.sh) lands the real per-request reap.
#
# Trips on gateway cgroup MemoryCurrent >= THRESH_MEM_G (Bes threshold lower
# than Lumen's 11G: Bes OOM'd at 13.1G with no headroom from other services,
# so 6G leaves margin before MemoryMax=8G forces a kernel OOM kill instead of
# this clean restart).
#
# Restart is SAFE: gateway is stateless (same verified posture as wq#380).
# Runs as root via mcp-pressure-guard-bes.timer (1 min). Cooldown prevents
# flapping during the regrowth window.
set -u
THRESH_MEM_G=${THRESH_MEM_G:-6}  # GiB, bes-mcp-failover cgroup MemoryCurrent
COOLDOWN=${COOLDOWN:-600}        # seconds between auto-restarts
STAMP=/run/mcp-pressure-guard-bes.last
UNIT=bes-mcp-failover

mem=$(systemctl show "$UNIT" -p MemoryCurrent --value 2>/dev/null)
case "$mem" in ''|*[!0-9]*) mem=0;; esac
memg=$(( mem / 1073741824 ))

[ "$memg" -ge "$THRESH_MEM_G" ] || exit 0

now=$(date +%s); last=$(cat "$STAMP" 2>/dev/null || echo 0)
case "$last" in ''|*[!0-9]*) last=0;; esac
if [ $((now - last)) -lt "$COOLDOWN" ]; then
  logger -t mcp-guard-bes "trip (gateway_mem=${memg}G) suppressed — cooldown ($((now-last))s < ${COOLDOWN}s)"
  exit 0
fi
echo "$now" > "$STAMP"

# wq#3299: verify the restart actually replaced the process (MainPID
# advances), not just that /health answers — a stuck process can still serve
# /health from a cached response while every real MCP call hangs.
pid_before=$(systemctl show "$UNIT" -p MainPID --value 2>/dev/null)
logger -t mcp-guard-bes "TRIP: gateway_mem=${memg}G (pid=$pid_before) — restarting $UNIT"
systemctl restart "$UNIT"
sleep 3
pid_after=$(systemctl show "$UNIT" -p MainPID --value 2>/dev/null)
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health || echo 000)
if [ "$pid_after" = "$pid_before" ] || [ -z "${pid_after:-}" ] || [ "$pid_after" = 0 ]; then
  logger -t mcp-guard-bes "RESTART DID NOT ADVANCE MainPID ($pid_before -> $pid_after) health=$hc"
else
  logger -t mcp-guard-bes "post-restart MainPID $pid_before -> $pid_after health=$hc"
fi
exit 0
