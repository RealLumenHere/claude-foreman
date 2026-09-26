#!/bin/bash
# wq#2751 apply. Needs root. Two parts, run separately:
#   apply.sh projects      unattended OK: projects-app loopback + handoff traversal fix
#   apply.sh control-plane Frank driving only (CONTROL-PLANE RULE 3): chrome-mcp/memory-mcp loopback,
#                          then a full MCP initialize through each CF hostname before walking away
# Rollback: apply.sh rollback-projects | rollback-control-plane
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
TS=20260926_wq2751
case "${1:-}" in
  projects)
    cp -p /home/lumen/projects_app.py /home/lumen/projects_app.py.bak.$TS
    install -o lumen -g lumen -m 0644 "$D/projects_app.py" /home/lumen/projects_app.py
    systemctl restart projects-app
    ;;
  control-plane)
    install -o lumen -g lumen -m 0644 "$D/loopback-listen.cjs" /home/lumen/bin/loopback-listen.cjs
    for u in chrome-mcp memory-mcp; do
      install -D -m 0644 "$D/$u.service.d/20-wq2751-loopback.conf" /etc/systemd/system/$u.service.d/20-wq2751-loopback.conf
    done
    systemctl daemon-reload
    systemctl restart chrome-mcp memory-mcp
    ;;
  rollback-projects)
    cp -p /home/lumen/projects_app.py.bak.$TS /home/lumen/projects_app.py
    systemctl restart projects-app
    ;;
  rollback-control-plane)
    rm -f /etc/systemd/system/chrome-mcp.service.d/20-wq2751-loopback.conf \
          /etc/systemd/system/memory-mcp.service.d/20-wq2751-loopback.conf
    systemctl daemon-reload
    systemctl restart chrome-mcp memory-mcp
    ;;
  *) echo "usage: $0 projects|control-plane|rollback-projects|rollback-control-plane" >&2; exit 2 ;;
esac
python3 "$D/test_wq2751.py" --live || true
