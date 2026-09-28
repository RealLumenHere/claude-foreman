#!/bin/sh
# wq#2997 — `wq done` agrees with `wq claim`'s 'user'-lane refusal.
#
# Keeper pattern (same as ops/wq1498-dead-worker-release): the live target is
# /home/lumen/bin/wq itself, patched in place by
# ensure_wq_user_lane_done_gate.py, not a file this bundle ships and copies.
# `wq` is rewritten whole by whichever lane touches it next, so re-applying is
# the normal case, not a one-time install.
#
# wq#3011 verify: this bundle shipped with no install.sh at all, so
# harness/liveness.py's ops_verified() could only ever report "no install.sh"
# for it (ops/VERIFY_CONTRACT.md) — no drift page, no [verify]-ticket
# Liveness: line could ever go SATISFIED. --verify below delegates to
# ensure_wq_user_lane_done_gate.py --check, which is already read-only (a
# string-count against the live file, no writes).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ENSURE="$HERE/ensure_wq_user_lane_done_gate.py"
WQ_TARGET="${WQ_BIN:-/home/lumen/bin/wq}"

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  if [ ! -e "$WQ_TARGET" ]; then
    echo "ERR absent: $WQ_TARGET"
    exit 2
  fi
  if [ ! -r "$WQ_TARGET" ]; then
    echo "ERR unreadable: $WQ_TARGET"
    exit 2
  fi
  CHECK_OUT="$(python3 "$ENSURE" --check --json --wq "$WQ_TARGET" 2>&1)" || true
  PATCHED="$(printf '%s' "$CHECK_OUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("parse-error")
    sys.exit(0)
print("yes" if d.get("patched") else "no")
' 2>/dev/null || echo "parse-error")"
  case "$PATCHED" in
    yes) ;;
    no)
      MISS_LINES="$(printf '%s' "$CHECK_OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for name, state in d.get("patches", {}).items():
    if state != "applied":
        print(f"MISS {name}: {state}")
' 2>/dev/null)" || MISS_LINES="MISS wq2997-user-lane-done-gate: not patched"
      printf '%s\n' "$MISS_LINES"
      MISS_COUNT="$(printf '%s\n' "$MISS_LINES" | grep -c '^MISS ')"
      _V_MISS=$((_V_MISS + MISS_COUNT))
      ;;
    *)
      echo "ERR ensure --check produced unparseable output: $CHECK_OUT"
      _V_ERR=1
      ;;
  esac
  vdone
fi

python3 "$ENSURE" --apply --wq "$WQ_TARGET"
