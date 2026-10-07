#!/usr/bin/env bash
# wq#3354 fleet-selfheal rollout. Run AS ROOT on Lumen.
#
#   install.sh --verify     read-only deploy check (ops/VERIFY_CONTRACT.md)
#   install.sh --no-enable  install code + units, print both dry-runs, enable nothing
#   install.sh              the above, then enable + start the sweep timer and leakwatch
#
# The router half (orchestrator/router.py: cooled rungs skipped in every pool) ships
# with the foreman merge itself; nothing to install for it.
#
# Rollback (whole layer):
#   systemctl disable --now lumen-fleet-selfheal.timer lumen-leakwatch.service
#   rm -f /etc/systemd/system/lumen-fleet-selfheal.{service,timer} \
#         /etc/systemd/system/lumen-leakwatch.service
#   systemctl daemon-reload
#   # then drop our cooldown rows: entries with "source": "fleet-selfheal" in
#   # /home/lumen/foreman/dispatcher/runtime/backend_cooldown.json, and any
#   # /home/lumen/agents/holddowns/*.json whose owner_agent is fleet-selfheal.
# Per-class rollback: `enabled: false` under that class in
#   /home/lumen/agents/fleet-selfheal/selfheal.yaml (no restart needed; read every sweep).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/agents/fleet-selfheal
UNITS=(lumen-fleet-selfheal.service lumen-fleet-selfheal.timer lumen-leakwatch.service)
CODE=(selfheal_common.py selfheal_sweep.py sh_guards.py sh_crashloop.py sh_disk.py
      sh_providers.py sh_nodes.py sh_ollama.py leakwatch.py)

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for f in "${CODE[@]}"; do vfile "$SRC/$f" "$DEST/$f"; done
  [ -e "$DEST/selfheal.yaml" ] || _vmiss "absent: $DEST/selfheal.yaml"
  for u in "${UNITS[@]}"; do vunit_file "$SRC/$u" "/etc/systemd/system/$u"; done
  venabled lumen-fleet-selfheal.timer
  vactive  lumen-fleet-selfheal.timer
  venabled lumen-leakwatch.service
  vactive  lumen-leakwatch.service
  vdone
fi

[ "$(id -u)" = 0 ] || { echo "must run as root (unit install + enable)"; exit 1; }

python3 -m pytest -q "$SRC/tests" >/dev/null 2>&1 \
  || { echo "REFUSING: $SRC/tests does not pass on this checkout"; exit 1; }

install -d -o lumen -g lumen -m 0755 "$DEST"
for f in "${CODE[@]}"; do install -o lumen -g lumen -m 0755 "$SRC/$f" "$DEST/$f"; done
# The live config is the operator's to edit; never clobber it.
[ -e "$DEST/selfheal.yaml" ] || install -o lumen -g lumen -m 0644 "$SRC/selfheal.yaml" "$DEST/selfheal.yaml"
for u in "${UNITS[@]}"; do install -m 0644 "$SRC/$u" "/etc/systemd/system/$u"; done
systemctl daemon-reload

echo "=== sweep dry-run (as root, touches nothing) ==="
python3 "$DEST/selfheal_sweep.py" --dry-run || true
echo "=== leakwatch start-up sweep dry-run (as lumen) ==="
runuser -u lumen -- python3 "$DEST/leakwatch.py" --once --dry-run || true

if [ "${1:-}" = --no-enable ]; then
  echo "installed, NOT enabled (--no-enable). Enable with: $0"
  exit 0
fi
systemctl enable --now lumen-fleet-selfheal.timer lumen-leakwatch.service
echo "enabled. Live check: $0 --verify"
