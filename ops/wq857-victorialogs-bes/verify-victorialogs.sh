#!/usr/bin/env bash
# wq#857 — read-only, no-privilege status check. Safe to run before install
# (baseline: VictoriaLogs port should be unreachable, datasource absent) and
# after (post-install: port answers, retention flag correct, datasource wired,
# journald rows arriving).
#
# Usage: verify-victorialogs.sh <tailscale-ip-of-bes>
set -uo pipefail

BES_ADDR="${1:?usage: verify-victorialogs.sh <tailscale-ip-of-bes>}"

echo "== VictoriaLogs HTTP reachability (http://${BES_ADDR}:9428) =="
if curl -s -m 5 -o /dev/null -w "http_code=%{http_code}\n" "http://${BES_ADDR}:9428/health"; then :; else echo "connection failed (expected pre-install)"; fi

echo "== retention flag (via /flags, if the process is up) =="
curl -s -m 5 "http://${BES_ADDR}:9428/flags" 2>/dev/null | grep -i retentionPeriod || echo "not reachable / flag not found"

echo "== log row count via LogsQL (last 5m) =="
curl -s -m 5 "http://${BES_ADDR}:9428/select/logsql/query" --data-urlencode 'query=_time:5m' 2>/dev/null | wc -l

echo "== Grafana health (http://${BES_ADDR}:3000) =="
curl -s -m 5 "http://${BES_ADDR}:3000/api/health" || echo "unreachable"

echo "== Grafana datasources =="
curl -s -m 5 "http://${BES_ADDR}:3000/api/datasources" 2>/dev/null | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    for x in d:
        print(x.get('name'), x.get('type'), x.get('url'))
except Exception as e:
    print('could not parse datasource list:', e)
"
