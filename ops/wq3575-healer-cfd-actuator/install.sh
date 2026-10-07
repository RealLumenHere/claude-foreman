#!/usr/bin/env bash
# wq#3575 — cfd-actuator rollout stage 2: the self-healer files a cloudflared
# restart request instead of restarting the unit itself.
# Live table: /home/lumen/agent/healer_watch_table.yaml (outside git; healer reloads
# on mtime). healer_watch_table.diff is the record of the edit. Runs as lumen.
# Idempotent: safe to re-run.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
TABLE="${HEALER_WATCH_TABLE:-/home/lumen/agent/healer_watch_table.yaml}"  # override: tests only
BIN="${CFD_HEAL_BIN:-/home/lumen/bin/cfd-heal-request}"                    # override: tests only
ROW='^[[:space:]]+cloudflared:'
CMD="restart_cmd: $BIN}"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/cfd-heal-request" "$BIN"
  [ -x "$BIN" ] || _vmiss "not executable: $BIN"
  if [ ! -r "$TABLE" ]; then
    echo "ERR target unreadable: $TABLE"; _V_ERR=1
  else
    grep -E -- "$ROW" "$TABLE" | grep -qF -- "$CMD" \
      || _vmiss "cloudflared row in $TABLE lacks $CMD"
    python3 -c 'import sys,yaml; u=yaml.safe_load(open(sys.argv[1]))["units"]; sys.exit(0 if u["cloudflared"].get("restart_cmd")==sys.argv[2] else 1)' "$TABLE" "$BIN" \
      || _vmiss "table does not parse to cloudflared.restart_cmd=$BIN: $TABLE"
  fi
  vdone
fi

echo "== wrapper -> $BIN =="
install -m 755 "$SRC/cfd-heal-request" "$BIN"

echo "== routing the healer's cloudflared row through cfd-actuator =="
grep -qE -- "$ROW" "$TABLE" || { echo "ERR no cloudflared row in $TABLE"; exit 1; }
if grep -E -- "$ROW" "$TABLE" | grep -qF -- "$CMD"; then
  echo "unchanged: $TABLE"
else
  cp -p "$TABLE" "$TABLE.pre-wq3575"
  sed -i -E "/$ROW/ s#\}[[:space:]]*\$#, $CMD#" "$TABLE"
  echo "patched $TABLE (previous copy: $TABLE.pre-wq3575)"
fi
python3 -c 'import sys,yaml; print("cloudflared ->", yaml.safe_load(open(sys.argv[1]))["units"]["cloudflared"])' "$TABLE"
