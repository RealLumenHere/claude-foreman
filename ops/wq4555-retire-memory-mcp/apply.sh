#!/bin/bash
# wq#4555 (wq#4539 phase 3): retire memory-mcp.service — 0 callers over 7d.
# Needs root, ATTENDED: systemctl disable and systemd-run are worker-denied by
# design, so a human-driven root session runs this; the disable itself goes
# through detached systemd-run (instinct 5 pattern).
#   apply.sh precheck   re-verify 0 callers + back up the drop-in dir (no changes)
#   apply.sh retire     precheck, then disable --now via detached systemd-run,
#                       remove the wq#2768 drop-in, verify :3104 refused
#   apply.sh mcp-entry  drop the fleet-memory MCP server entry from ~/.claude.json
#                       (run as lumen, not root)
#   apply.sh rollback   re-enable the unit + restore drop-ins from latest backup
# The unit file stays in place for 1 WEEK after retire. If anything logs to the
# unit in that week, roll back. After a quiet week, remove the unit file by hand.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
BK=/home/lumen/backups/wq4555-memory-mcp
TS=$(date +%Y%m%d_%H%M%S)

precheck() {
  echo "== precheck: callers must be 0 before retiring =="
  echo "-- journal entries, trailing 7d (expect '-- No entries --'):"
  journalctl -u memory-mcp --since "7 days ago" --no-pager | tail -5
  echo "-- current :3104 listener (informational):"
  ss -tlnH 'sport = :3104' || true
  mkdir -p "$BK"
  if [ -d /etc/systemd/system/memory-mcp.service.d ]; then
    cp -a /etc/systemd/system/memory-mcp.service.d "$BK/memory-mcp.service.d.$TS"
    echo "-- drop-ins backed up to $BK/memory-mcp.service.d.$TS"
  fi
}

case "${1:-}" in
  precheck) precheck ;;
  retire)
    precheck
    # Detached: systemd-run returns immediately, the disable runs in its own unit.
    systemd-run --collect --unit="wq4555-retire-memory-mcp-$TS" \
      systemctl disable --now memory-mcp
    sleep 3
    echo "-- is-enabled (expect disabled): $(systemctl is-enabled memory-mcp || true)"
    echo "-- is-active  (expect inactive): $(systemctl is-active memory-mcp || true)"
    rm -f /etc/systemd/system/memory-mcp.service.d/30-wq2768-access-guard.conf
    systemctl daemon-reload
    echo "-- :3104 post-retire (expect refused):"
    curl -s -o /dev/null -w '%{http_code}\n' --max-time 3 http://127.0.0.1:3104/health \
      || echo "refused (good)"
    python3 "$D/../wq2751-listener-audit/test_wq2751.py" --live || true
    echo "== unit file kept; watch 'journalctl -u memory-mcp' for 1 week before removing it ==" ;;
  mcp-entry)
    python3 "$D/remove_mcp_entry.py" ;;
  rollback)
    LATEST=$(ls -1d "$BK"/memory-mcp.service.d.* 2>/dev/null | tail -1)
    if [ -n "${LATEST:-}" ]; then
      mkdir -p /etc/systemd/system/memory-mcp.service.d
      cp -a "$LATEST"/. /etc/systemd/system/memory-mcp.service.d/
      echo "-- drop-ins restored from $LATEST"
    fi
    systemctl daemon-reload
    systemd-run --collect --unit="wq4555-rollback-memory-mcp-$TS" \
      systemctl enable --now memory-mcp
    sleep 3
    echo "-- is-active (expect active): $(systemctl is-active memory-mcp || true)"
    echo "NOTE: rollback re-opens :3104 on loopback; revert the wq#4555 inversion in"
    echo "ops/wq2751-listener-audit/test_wq2751.py before the next --live audit run." ;;
  *) echo "usage: $0 precheck|retire|mcp-entry|rollback" >&2; exit 2 ;;
esac
