#!/bin/bash
# wq#2768 apply. Needs root. CONTROL-PLANE RULE 3: Frank driving.
#   apply.sh deploy     install guard + 30-* drop-ins, restart chrome-mcp memory-mcp, verify
#   apply.sh rollback   remove the 30-* drop-ins (wq#2751 loopback stays), restart, verify
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
verify() {
  for p in 3103 3104; do
    for i in $(seq 1 40); do curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$p/health" && break; sleep 0.5; done
    printf ":%s health=%s " $p "$(curl -s -o /dev/null -w %{http_code} --max-time 5 http://127.0.0.1:$p/health)"
    printf "anon_mcp=%s bind=%s\n" "$(curl -s -o /dev/null -w %{http_code} --max-time 5 -X POST -H "content-type: application/json" -d "{}" http://127.0.0.1:$p/mcp)" \
      "$(ss -tlnH "sport = :$p" | awk "{print \$4}" | paste -sd,)"
  done
}
case "${1:-}" in
  deploy)
    runuser -u lumen -- env HOME=/home/lumen node "$D/test_access_guard.mjs" >/dev/null || { echo "offline tests FAIL, not deploying" >&2; exit 1; }
    install -o lumen -g lumen -m 0644 "$D/access-jwt-guard.cjs" /home/lumen/bin/access-jwt-guard.cjs
    for u in chrome-mcp memory-mcp; do
      install -D -m 0644 "$D/$u.service.d/30-wq2768-access-guard.conf" /etc/systemd/system/$u.service.d/30-wq2768-access-guard.conf
    done
    systemctl daemon-reload; systemctl restart chrome-mcp memory-mcp; verify ;;
  rollback)
    rm -f /etc/systemd/system/chrome-mcp.service.d/30-wq2768-access-guard.conf /etc/systemd/system/memory-mcp.service.d/30-wq2768-access-guard.conf
    systemctl daemon-reload; systemctl restart chrome-mcp memory-mcp; verify ;;
  *) echo "usage: $0 deploy|rollback" >&2; exit 2 ;;
esac
