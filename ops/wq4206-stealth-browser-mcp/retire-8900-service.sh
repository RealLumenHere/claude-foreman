#!/usr/bin/env bash
# wq#4290: retire stealth-browser-mcp.service, the second, unguarded deployment: raw
# upstream server.py over HTTP on 127.0.0.1:8900 from /home/lumen/projects/stealth-browser-mcp,
# 101 tools listed (its fastmcp ignores --minimal) incl. execute_cdp_command,
# execute_python_in_browser and execute_script, none behind lumen_stealth_guard. The
# guarded stdio install (RUNBOOK.md) supersedes it; it had no consumers on 2026-10-18.
#
# Root, detached, after orchestrator sign-off:
#   lumen-run start --root --name wq4290-retire-8900 -- ops/wq4206-stealth-browser-mcp/retire-8900-service.sh
# Undo: --restore puts the unit file back and starts it again.
# /home/lumen/projects/stealth-browser-mcp is left in place either way.
set -euo pipefail
U=stealth-browser-mcp.service
LIVE=/etc/systemd/system/$U
PARKED=/etc/systemd/system/$U.retired-wq4290

if [ "${1:-}" = --restore ]; then
  mv "$PARKED" "$LIVE"
  systemctl daemon-reload
  systemctl enable --now "$U"
  sleep 3
  systemctl is-active "$U"
  ss -ltnH 'sport = :8900' | grep -q . && echo "restored: $U listening on :8900"
  exit 0
fi

systemctl disable --now "$U"
mv "$LIVE" "$PARKED"
systemctl daemon-reload
if ss -ltnH 'sport = :8900' | grep -q .; then
  echo "FAIL: something still listens on :8900" >&2
  exit 1
fi
echo "retired: $U stopped and disabled; unit parked at $PARKED; :8900 closed"
