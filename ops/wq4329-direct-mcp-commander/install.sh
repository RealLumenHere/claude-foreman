#!/usr/bin/env bash
# wq#4329 — Phase 1 direct MCP: stage the per-node Remote Desktop Commander
# unit for Lumen or Maksoor. Idempotent: safe to re-run.
#
# What this does NOT do, deliberately:
#   - does not run the OAuth device-flow pairing (needs a human in a browser,
#     see PHASE1-PAIRING-CHECKLIST.md) -- a physical task, not something this
#     script can satisfy.
#   - does not daemon-reload/enable/start the unit -- writing into
#     /etc/systemd/system requires root, which a BUILD worker sandbox does not
#     have (same gap as ISSUE-LOG #731); this stages the unit file for an
#     authorized deploy path to pick up.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
NODE_USER="${1:?usage: install.sh <node-user> (e.g. lumen or maksoor)}"
DEST="${REMOTE_DC_UNIT_DEST:-/etc/systemd/system/remote-desktop-commander.service}"  # override: tests only
STAGE="${REMOTE_DC_STAGE_DIR:-/home/${NODE_USER}/staged-units}"

if [ "${2:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if ! command -v node >/dev/null 2>&1; then
    _vmiss "node not found on PATH -- Remote Desktop Commander needs Node.js >= 18"
  else
    NODE_MAJOR="$(node -e 'process.stdout.write(String(process.versions.node.split(".")[0]))')"
    [ "$NODE_MAJOR" -ge 18 ] 2>/dev/null || _vmiss "node major version $NODE_MAJOR < 18"
  fi
  [ -f "$STAGE/remote-desktop-commander.service" ] || _vmiss "staged unit missing: $STAGE/remote-desktop-commander.service"
  grep -q "User=${NODE_USER}\$" "$STAGE/remote-desktop-commander.service" 2>/dev/null || _vmiss "staged unit not templated for $NODE_USER"
  vdone
fi

mkdir -p "$STAGE"
sed "s/__NODE_USER__/${NODE_USER}/g" "$SRC/remote-desktop-commander.service" > "$STAGE/remote-desktop-commander.service"
echo "staged: $STAGE/remote-desktop-commander.service (destined for $DEST once Frank pairs the device and an authorized deploy path installs it)"
