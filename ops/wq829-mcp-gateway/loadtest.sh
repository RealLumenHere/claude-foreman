#!/usr/bin/env bash
# wq#829 test-to-failure — drive N parallel MCP clients at the gateway, ramping N
# until something breaks, then report the safe concurrent-client ceiling.
# Run ONLY in a quiet window (no chats in flight) — this deliberately saturates
# the gateway. Each request is a full stateless lifecycle: spawn child,
# auto-initialize, tools/list, reap. Errors (not 503 sheds) mark the ceiling.
set -u
URL=${URL:-http://127.0.0.1:3100/mcp}
HEALTH=${HEALTH:-http://127.0.0.1:3100/health}
RAMP=${RAMP:-"4 8 12 16 24 32 48"}
REQS_PER_CLIENT=${REQS_PER_CLIENT:-5}
OUT=${OUT:-/tmp/wq829_loadtest}
mkdir -p "$OUT"
BODY='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

recent=$(journalctl -u desktop-commander-mcp --since "10 min ago" --no-pager 2>/dev/null | grep -c "StreamableHttp → Child" || true)
if [ "${recent:-0}" -gt "${REQS_PER_CLIENT}" ] && [ "${WQ829_FORCE:-0}" != 1 ]; then
  echo "REFUSING: gateway served $recent requests in last 10 min — not a quiet window (WQ829_FORCE=1 to override)"
  exit 1
fi

client() { # $1=phase-file
  local i code t
  for i in $(seq "$REQS_PER_CLIENT"); do
    t=$(curl -s -m 120 -o /dev/null -w '%{http_code} %{time_total}' \
      -X POST "$URL" \
      -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
      -d "$BODY") || t="000 timeout"
    echo "$t" >> "$1"
  done
}

echo "N  ok  shed503  errors  p95s  psi_full_avg10  mem_after  guard_trips"
ceiling=0
for N in $RAMP; do
  phase="$OUT/phase_$N.txt"; : > "$phase"
  start_ts=$(date +%s)
  mark=$(date -u '+%Y-%m-%d %H:%M:%S')
  for _ in $(seq "$N"); do client "$phase" & done
  wait
  ok=$(grep -c '^200 ' "$phase" || true)
  shed=$(grep -c '^503 ' "$phase" || true)
  total=$(wc -l < "$phase")
  err=$((total - ok - shed))
  p95=$(awk '{print $2}' "$phase" | sort -n | awk -v n="$total" 'NR==int(n*0.95)+((n*0.95==int(n*0.95))?0:1){print; exit}')
  psi=$(awk '/^full/{for(i=1;i<=NF;i++)if($i~/^avg10=/){sub("avg10=","",$i);print $i;exit}}' /proc/pressure/memory)
  mem=$(systemctl show desktop-commander-mcp -p MemoryCurrent --value)
  trips=$(journalctl -t mcp-guard --since "$mark" --no-pager 2>/dev/null | grep -c TRIP || true)
  hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$HEALTH" || echo 000)
  echo "$N  $ok  $shed  $err  ${p95:-?}  $psi  $mem  $trips  (health=$hc, $(($(date +%s)-start_ts))s)"
  if [ "$err" -gt 0 ] || [ "${trips:-0}" -gt 0 ] || [ "$hc" != 200 ]; then
    echo "BROKE at N=$N (errors=$err trips=$trips health=$hc). Ceiling: last clean N=$ceiling"
    exit 0
  fi
  [ "$shed" -eq 0 ] && ceiling=$N
  sleep 15   # let PSI avg10 decay between phases so results don't bleed
done
echo "Survived full ramp. Highest N with zero sheds and zero errors: $ceiling"
