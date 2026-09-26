#!/bin/bash
# wq#2770: --verify handler for wq#2751 (ops/VERIFY_CONTRACT.md). Actual deploy logic stays in
# apply.sh (projects|control-plane|rollback-*) -- this file only adds the contract-required
# entry point ops-deploy-drift looks for (it hardcodes the filename "install.sh", so a --verify
# arm on apply.sh alone would never be discovered by the automatic sweep).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  # part 1: projects (unattended, expected live)
  vfile "$SRC/projects_app.py" /home/lumen/projects_app.py
  vactive projects-app

  # part 2: control-plane (Frank-driven, CONTROL-PLANE RULE 3 -- may legitimately not be
  # applied yet; MISS here just means "not deployed yet", not "broken")
  vfile "$SRC/loopback-listen.cjs" /home/lumen/bin/loopback-listen.cjs
  vunit_file "$SRC/chrome-mcp.service.d/20-wq2751-loopback.conf" \
             /etc/systemd/system/chrome-mcp.service.d/20-wq2751-loopback.conf
  vunit_file "$SRC/memory-mcp.service.d/20-wq2751-loopback.conf" \
             /etc/systemd/system/memory-mcp.service.d/20-wq2751-loopback.conf
  vactive chrome-mcp
  vactive memory-mcp

  # the file+unit checks above prove the drop-in is INSTALLED, not that the process actually
  # bound to loopback -- confirm the live socket directly (no _verify.sh helper for this yet).
  for portsvc in "3103 chrome-mcp" "3104 memory-mcp"; do
    set -- $portsvc
    port="$1"; svc="$2"
    if ss -tlnH "sport = :$port" 2>/dev/null | grep -q "^LISTEN.*:$port "; then
      if ss -tlnH "sport = :$port" 2>/dev/null | awk '{print $4}' | grep -qv "^127\.0\.0\.1:"; then
        _vmiss "$svc :$port is listening but NOT loopback-only (still bound wide)"
      fi
    fi
  done

  vdone
fi

echo "install.sh only implements --verify for this bundle." >&2
echo "For actual deployment use: $SRC/apply.sh {projects|control-plane|rollback-projects|rollback-control-plane}" >&2
exit 2
