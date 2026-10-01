#!/usr/bin/env bash
# mcp-pressure-guard-bes — auto-recover bes-mcp-failover (supergateway+DC) from
# handler-leak saturation (wq#3299). Bes port differs from Lumen's
# desktop-commander-mcp (3100 here, same as Lumen's own gateway, but a
# different unit/host) and the trip threshold is lower (~6G, matching the
# MemoryHigh drop-in in this same ops dir) since Bes's box has less headroom
# than Lumen's — the 2026-09-30 OOM took the whole host down at 13.1GB with no
# MemoryMax in place at all.
# Restart is SAFE: gateway is stateless per-request (ticket wq#3299).
# Runs as root via mcp-pressure-guard-bes.timer (1 min). Cooldown prevents flapping.
set -u
UNIT=${UNIT:-bes-mcp-failover}
HEALTH_PORT=${HEALTH_PORT:-3100}
THRESH_PSI=${THRESH_PSI:-25}     # percent, /proc/pressure/memory 'full avg60'
THRESH_MEM_G=${THRESH_MEM_G:-6}  # GiB, gateway cgroup MemoryCurrent (wq#3299: MemoryHigh=6G)
COOLDOWN=${COOLDOWN:-600}        # seconds between auto-restarts
STAMP=/run/mcp-pressure-guard-bes.last
NTFY_URL=http://127.0.0.1:8080/lumen-fleet

psi=$(awk '/^full/{for(i=1;i<=NF;i++)if($i ~ /^avg60=/){sub("avg60=","",$i);print $i;exit}}' /proc/pressure/memory)
mem=$(systemctl show "$UNIT" -p MemoryCurrent --value 2>/dev/null)
case "$mem" in ''|*[!0-9]*) mem=0;; esac
memg=$(( mem / 1073741824 ))

trip=""
awk -v p="${psi:-0}" -v t="$THRESH_PSI" 'BEGIN{exit !(p+0>=t+0)}' && trip="psi_full_avg60=${psi}"
[ "$memg" -ge "$THRESH_MEM_G" ] && trip="${trip:+$trip }gateway_mem=${memg}G"
[ -z "$trip" ] && exit 0

now=$(date +%s); last=$(cat "$STAMP" 2>/dev/null || echo 0)
case "$last" in ''|*[!0-9]*) last=0;; esac
if [ $((now - last)) -lt "$COOLDOWN" ]; then
  logger -t mcp-guard-bes "trip ($trip) suppressed — cooldown ($((now-last))s < ${COOLDOWN}s)"
  exit 0
fi
echo "$now" > "$STAMP"

pid_before=$(systemctl show "$UNIT" -p MainPID --value 2>/dev/null)
logger -t mcp-guard-bes "TRIP: $trip — restarting $UNIT (MainPID was $pid_before)"
systemctl restart "$UNIT"
sleep 3
pid_after=$(systemctl show "$UNIT" -p MainPID --value 2>/dev/null)
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${HEALTH_PORT}/health" || echo 000)
# wq#3299: a dead/respawning child can still answer /health via a stale process
# or a different listener on the same port, so confirm the unit actually
# produced a NEW MainPID, not just a 200.
if [ -n "$pid_after" ] && [ "$pid_after" != "0" ] && [ "$pid_after" != "$pid_before" ]; then
  advanced="yes"
else
  advanced="no"
fi
logger -t mcp-guard-bes "post-restart health=$hc mainpid_advanced=$advanced ($pid_before -> $pid_after)"
curl -sf -m 5 -d "mcp-guard-bes: auto-restarted $UNIT ($trip) health=$hc mainpid_advanced=$advanced $(date -u +%H:%M:%SZ)" "$NTFY_URL" >/dev/null 2>&1 || true
exit 0
