#!/usr/bin/env bash
# dc-redaction-gate.sh -- is the redaction code actually LIVE in the running desktop-commander?
#
# Why (wq#3804): wq#3645 (redact on output, 2026-10-08) and wq#3276 (handle-store write-time
# redaction, 2026-10-11) were closed as deployed, but the running DC child had started 2026-10-07,
# BEFORE both files landed on disk. Node loads dist/*.js once at start, so the patches were inert
# until dc-gateway was restarted on 2026-10-13: for ~6 days live output was unredacted. A patch on
# disk is not a patch in memory.
#
# Two checks, both read-only (this script NEVER restarts anything):
#   1. staleness (always): the OLDEST live DC child of <unit> (any child that predates a patch serves
#      unredacted output) must have started AFTER the newest mtime of the redaction-bearing dist
#      files (redact.js terminal-manager.js handle-store.js). A missing file also fails (no
#      redaction code installed at all, e.g. an unpatched standby).
#   2. live probe (--probe PORT, optional): real MCP round trip against 127.0.0.1:PORT/mcp that
#      echoes a synthetic AWS-key-shaped token through start_process; PASS only if the reply
#      contains "<redacted" and NOT the raw token. The token is assembled at runtime, so this
#      file carries no secret-shaped literal.
#
# Exit: 0 live+fresh   1 STALE / MISSING / PROBE-FAIL (needs a dc-gateway restart or a patch)
#       2 cannot determine (unit not running, no DC child, dist dir not found)
# Output: one line "DC_REDACTION_GATE <OK|STALE|PROBE_FAIL|UNKNOWN> ...".
#
# Usage: dc-redaction-gate.sh [--unit dc-gateway] [--dist DIR] [--probe PORT] [--pid N]
#   --pid N  check this PID instead of resolving the unit's descendants (tests)
set -u
UNIT=${DCG_UNIT:-dc-gateway}
DIST=${DCG_DIST:-}
PROBE_PORT=""
PID_OVERRIDE=""
FILES=${DCG_FILES:-"redact.js terminal-manager.js handle-store.js"}

while [ $# -gt 0 ]; do
  case "$1" in
    --unit) UNIT=${2:-}; shift 2 ;;
    --dist) DIST=${2:-}; shift 2 ;;
    --probe) PROBE_PORT=${2:-}; shift 2 ;;
    --pid) PID_OVERRIDE=${2:-}; shift 2 ;;
    -h|--help) sed -n 2,26p "$0"; exit 0 ;;
    *) echo "dc-redaction-gate: unknown arg: $1" >&2; exit 2 ;;
  esac
done

unknown() { echo "DC_REDACTION_GATE UNKNOWN $*"; exit 2; }

# --- find the live DC children of the unit ---
descendants() {
  ps -e -o pid=,ppid= 2>/dev/null | awk -v r="$1" '{p[$1]=$2} END{for(i in p){x=i; n=0; while(x!="" && x!=0 && x!=1 && n<64){ if(x==r){print i; break}; x=p[x]; n++ }}}'
}
pids=""
if [ -n "$PID_OVERRIDE" ]; then
  pids=$PID_OVERRIDE
else
  main=$(systemctl show -p MainPID --value "$UNIT" 2>/dev/null)
  case "$main" in ''|0|*[!0-9]*) unknown "unit $UNIT has no MainPID (not running?)" ;; esac
  for p in $(descendants "$main"); do
    { tr "\0" " " < "/proc/$p/cmdline"; } 2>/dev/null | grep -q "desktop-commander" && pids="$pids $p"
  done
  [ -n "$pids" ] || unknown "no desktop-commander child under $UNIT (MainPID $main)"
fi

# oldest child = smallest start epoch (worst case: it predates a patch the soonest)
now=$(date +%s)
oldest_start=""; oldest_pid=""
for p in $pids; do
  et=$(ps -o etimes= -p "$p" 2>/dev/null | tr -d " ")
  case "$et" in ''|*[!0-9]*) continue ;; esac
  st=$((now - et))
  if [ -z "$oldest_start" ] || [ "$st" -lt "$oldest_start" ]; then oldest_start=$st; oldest_pid=$p; fi
done
[ -n "$oldest_start" ] || unknown "could not read start time for DC child ($pids)"

# --- locate the dist dir: --dist/DCG_DIST > the RUNNING child's own command line (exactly what is loaded;
# works from systemd where HOME/PATH are bare and the user is root) > PATH/HOME guess (tests with --pid) ---
if [ -z "$DIST" ]; then
  for tok in $({ tr "\0" " " < "/proc/$oldest_pid/cmdline"; } 2>/dev/null); do
    case "$tok" in
      */desktop-commander|*/dist/index.js) real=$(readlink -f "$tok" 2>/dev/null) && [ -f "$real" ] && { DIST=$(dirname "$real"); break; } ;;
    esac
  done
fi
if [ -z "$DIST" ]; then
  for bin in "$(command -v desktop-commander 2>/dev/null)" "${HOME:-/nonexistent}/.npm-global/bin/desktop-commander"; do
    [ -n "$bin" ] && [ -e "$bin" ] || continue
    real=$(readlink -f "$bin" 2>/dev/null) || continue
    [ -f "$real" ] && { DIST=$(dirname "$real"); break; }
  done
fi
[ -n "$DIST" ] && [ -d "$DIST" ] || unknown "dist dir not found (set --dist)"

# --- compare against the redaction-bearing files ---
newest=0; newest_f=""; missing=""
for f in $FILES; do
  if [ ! -f "$DIST/$f" ]; then missing="$missing $f"; continue; fi
  m=$(stat -c %Y "$DIST/$f")
  [ "$m" -gt "$newest" ] && { newest=$m; newest_f=$f; }
done
fmt() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }

if [ -n "$missing" ]; then
  echo "DC_REDACTION_GATE STALE missing_from_dist:${missing# } dist=$DIST (no redaction code installed)"
  exit 1
fi
if [ "$newest" -gt "$oldest_start" ]; then
  echo "DC_REDACTION_GATE STALE child_pid=$oldest_pid started=$(fmt "$oldest_start") but $newest_f modified=$(fmt "$newest") -- restart $UNIT (detached, with deadman) so the patch is loaded"
  exit 1
fi

# --- optional live probe ---
if [ -n "$PROBE_PORT" ]; then
  res=$(python3 - "$PROBE_PORT" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
url = f"http://127.0.0.1:{port}/mcp"
sid = {"v": None}
def post(body):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if sid["v"]:
        h["mcp-session-id"] = sid["v"]
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers=h, method="POST"), timeout=25)
    if r.headers.get("mcp-session-id"):
        sid["v"] = r.headers.get("mcp-session-id")
    return r.read().decode("utf-8", "replace")
token = "AKIA" + "Z9Y8X7W6" + "V5U4T3S2"   # synthetic, assembled at runtime
try:
    post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "dc-redaction-gate", "version": "1"}}})
    post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    raw = post({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "start_process", "arguments": {"command": "echo " + token, "timeout_ms": 8000}}})
    if token in raw:
        print("LEAK raw token present in reply")
    elif "<redacted" in raw:
        print("OK")
    else:
        print("INCONCLUSIVE no token and no redaction marker in reply")
except Exception as e:
    print("ERROR " + type(e).__name__ + ": " + str(e)[:120])
PY
)
  case "$res" in
    OK) ;;
    ERROR*) echo "DC_REDACTION_GATE UNKNOWN probe on :$PROBE_PORT failed to run: $res"; exit 2 ;;
    *) echo "DC_REDACTION_GATE PROBE_FAIL port=$PROBE_PORT $res -- live DC is NOT redacting; restart $UNIT"; exit 1 ;;
  esac
  echo "DC_REDACTION_GATE OK child_pid=$oldest_pid started=$(fmt "$oldest_start") newest_patch=$newest_f@$(fmt "$newest") probe=pass port=$PROBE_PORT"
  exit 0
fi

echo "DC_REDACTION_GATE OK child_pid=$oldest_pid started=$(fmt "$oldest_start") newest_patch=$newest_f@$(fmt "$newest") probe=skipped"
exit 0
