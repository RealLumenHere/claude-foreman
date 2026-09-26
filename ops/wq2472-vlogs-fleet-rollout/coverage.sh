#!/bin/bash
# wq#2472: per-node lossless check over a CLOSED window: journal line count (root, all journals) vs VictoriaLogs count for host.
S="${1:-2026-09-26 01:02:00}"; U="${2:-2026-09-26 01:04:00}"
SI=$(date -u -d "$S" +%Y-%m-%dT%H:%M:%SZ); UI=$(date -u -d "$U" +%Y-%m-%dT%H:%M:%SZ)
for T in anubis@100.121.17.5 hathor@100.80.71.13 isis@100.100.129.24 maam@100.105.219.34 sobek@100.86.156.91 thoth@100.75.1.108 khepri@100.120.16.78 bes@100.110.156.91; do
  read H J < <(timeout 30 ssh -o BatchMode=yes $T "echo \$(hostname) \$(sudo -n journalctl --since '$S' --until '$U' -o json --no-pager | wc -l)")
  V=$(curl -s --max-time 20 http://100.110.156.91:9428/select/logsql/stats_query --data-urlencode "query=host:\"$H\" AND _time:[$SI, $UI) | stats count() c" --data-urlencode "time=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | python3 -c 'import sys,json;r=json.load(sys.stdin)["data"]["result"];print(r[0]["value"][1] if r else 0)')
  [ "$J" = "$V" ] && v=OK || v=MISMATCH
  echo "$H journal=$J vl=$V $v"
done
