#!/usr/bin/env bash
# wq#2605 test-to-failure — run AS ROOT, after apply.sh, on Lumen. Reproduces
# the 04:28Z worker-storm shape (stress-ng --cpu 44 --io 8, i.e. saturate every
# core plus 8 IO stressors) INSIDE lumen-workers.slice, while polling the
# control plane's own MCP endpoint once a second for the whole window, and
# reports the tools/list p95. Pass criterion (ticket): p95 < 5s throughout.
#
# Usage: stress_test.sh [duration_seconds]   (default 300, matches the ticket)
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "must run as root (systemd-run --slice + stress-ng)"; exit 1; }
command -v stress-ng >/dev/null 2>&1 || { echo "stress-ng not installed — apt install stress-ng"; exit 1; }

DURATION="${1:-300}"
OUTDIR=$(mktemp -d /tmp/wq2605-stress.XXXXXX)
LATFILE="$OUTDIR/latencies.txt"
: > "$LATFILE"

echo "stress-ng --cpu 44 --io 8 -t ${DURATION}s inside lumen-workers.slice, results in $OUTDIR"

systemd-run --collect --unit=wq2605-stress-test --slice=lumen-workers.slice \
  stress-ng --cpu 44 --io 8 -t "${DURATION}s"

start=$(date +%s)
end=$((start + DURATION + 10))
while [ "$(date +%s)" -lt "$end" ]; do
  t0=$(date +%s.%N)
  code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' \
    -X POST http://127.0.0.1:3100/mcp \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' || echo 000)
  t1=$(date +%s.%N)
  elapsed=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.3f", b-a}')
  echo "$elapsed $code" >> "$LATFILE"
  sleep 1
done
# Loop runs the full DURATION+10s window regardless of when stress-ng's own
# -t timer ends, so the tail (recovery under draining load) is captured too.

n=$(grep -c ^ "$LATFILE" || echo 0)
p95=$(awk '{print $1}' "$LATFILE" | sort -n | awk -v n="$n" 'BEGIN{i=int(n*0.95); if(i<1)i=1} {a[NR]=$1} END{print a[i]}')
maxlat=$(awk '{print $1}' "$LATFILE" | sort -n | tail -1)
failed=$(awk '$2!="200"' "$LATFILE" | grep -c ^ || echo 0)

echo "--- wq#2605 stress result ($n samples over ~${DURATION}s) ---"
echo "p95 latency: ${p95}s (target < 5s)"
echo "max latency: ${maxlat}s"
echo "non-200 responses: $failed"
echo "raw samples: $LATFILE"
[ "$failed" = 0 ] && awk -v p="$p95" 'BEGIN{exit !(p<5)}' && echo "PASS" || echo "FAIL — record these numbers in wq#2605"
