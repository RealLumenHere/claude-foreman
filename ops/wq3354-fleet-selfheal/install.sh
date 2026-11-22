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
#   systemctl disable --now lumen-fleet-selfheal.timer lumen-leakwatch.service \
#                           lumen-exposure-census.timer
#   rm -f /etc/systemd/system/lumen-fleet-selfheal.{service,timer} \
#         /etc/systemd/system/lumen-leakwatch.service \
#         /etc/systemd/system/lumen-exposure-census.{service,timer}
#   systemctl daemon-reload
#   # then drop our cooldown rows: entries with "source": "fleet-selfheal" in
#   # /home/lumen/foreman/dispatcher/runtime/backend_cooldown.json, and any
#   # /home/lumen/agents/holddowns/*.json whose owner_agent is fleet-selfheal.
# Per-class rollback: `enabled: false` under that class in
#   /home/lumen/agents/fleet-selfheal/selfheal.yaml (no restart needed; read every sweep).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
# SELFHEAL_DEST override exists so tests can point --verify at a fixture tree
# (wq#4445: a verify that can only look at the real live dir can never be
# tested to failure).
DEST="${SELFHEAL_DEST:-/home/lumen/agents/fleet-selfheal}"
UNITS=(lumen-fleet-selfheal.service lumen-fleet-selfheal.timer lumen-leakwatch.service
       lumen-exposure-census.service lumen-exposure-census.timer)
CODE=(selfheal_common.py selfheal_sweep.py sh_guards.py sh_crashloop.py sh_disk.py
      sh_providers.py sh_nodes.py sh_ollama.py leakwatch.py exposure_census.py
      selfheal_merge.py)

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for f in "${CODE[@]}"; do vfile "$SRC/$f" "$DEST/$f"; done
  # wq#4445: presence alone was a false green — seed keys added after first
  # deploy never reached the operator copy. Fail on any seed key path (and
  # nodes.ignore/sleepers membership) missing live; live-only keys stay INFO.
  if [ -e "$DEST/selfheal.yaml" ]; then
    rc=0
    out="$(python3 "$SRC/selfheal_merge.py" --check \
             --seed "$SRC/selfheal.yaml" --live "$DEST/selfheal.yaml" 2>&1)" || rc=$?
    [ -z "$out" ] || echo "$out"
    if [ "$rc" = 1 ]; then
      n="$(printf '%s\n' "$out" | grep -c '^MISS ')" || true
      _V_MISS=$((_V_MISS + ${n:-1}))
    elif [ "$rc" != 0 ]; then
      echo "ERR selfheal_merge.py --check rc=$rc"; _V_ERR=1
    fi
  else
    _vmiss "absent: $DEST/selfheal.yaml"
  fi
  for u in "${UNITS[@]}"; do vunit_file "$SRC/$u" "/etc/systemd/system/$u"; done
  venabled lumen-fleet-selfheal.timer
  vactive  lumen-fleet-selfheal.timer
  vtimer_scheduled lumen-fleet-selfheal.timer  # wq#4910: vactive also passes SubState=elapsed
  venabled lumen-leakwatch.service
  vactive  lumen-leakwatch.service
  venabled lumen-exposure-census.timer
  vactive  lumen-exposure-census.timer
  vtimer_scheduled lumen-exposure-census.timer  # wq#4910: vactive also passes SubState=elapsed
  vdone
fi

[ "$(id -u)" = 0 ] || { echo "must run as root (unit install + enable)"; exit 1; }

# wq#4815: as root, python3 has no HOME and can't see lumen's --user site-packages
# (e.g. ruamel.yaml, imported by selfheal_merge.py) — run the test suite as lumen,
# same as the merge step below already does, not as root.
runuser -u lumen -- python3 -m pytest -q "$SRC/tests" >/dev/null 2>&1 \
  || { echo "REFUSING: $SRC/tests does not pass on this checkout"; exit 1; }

install -d -o lumen -g lumen -m 0755 "$DEST"
for f in "${CODE[@]}"; do install -o lumen -g lumen -m 0755 "$SRC/$f" "$DEST/$f"; done
# The live config is the operator's to edit; never clobber it. wq#4445: but DO
# merge NEW seed keys in (never overwriting existing ones — operator wins on
# values): the script backs up, keeps comments, validates before replacing.
if [ -e "$DEST/selfheal.yaml" ]; then
  runuser -u lumen -- python3 "$SRC/selfheal_merge.py" \
    --seed "$SRC/selfheal.yaml" --live "$DEST/selfheal.yaml"
else
  install -o lumen -g lumen -m 0644 "$SRC/selfheal.yaml" "$DEST/selfheal.yaml"
fi
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
systemctl enable --now lumen-fleet-selfheal.timer lumen-leakwatch.service \
                       lumen-exposure-census.timer
echo "enabled. Live check: $0 --verify"
