#!/usr/bin/env bash
# wq#2571 — offline test for litellm-auth-monitor's line-processing logic.
# No docker/container required: sources the script in TEST_MODE (which skips
# the `run` main loop) and drives process_line() directly against fixture
# lines, exactly the way the real reconnect loop would.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/litellm-auth-monitor"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

export LITELLM_AUTH_LOGDIR="$WORK"
export LITELLM_AUTH_MONITOR_TEST_MODE=1

# shellcheck source=litellm-auth-monitor
source "$SCRIPT"

LOGFILE="$WORK/auth-failures.jsonl"

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Test 1: a raw access-log line containing a backslash must still
#     produce valid, parseable JSONL (the old printf/${//\"/\\\"} path only
#     escaped quotes, so a backslash produced a broken \-escape).
FIXTURE1=$(mktemp)
printf '%s\n' 'irrelevant line' \
              '2026-09-25T00:00:01.000000000Z INFO:     1.2.3.4:5555 - "GET /v1\models HTTP/1.1" 401 Unauthorized' \
              > "$FIXTURE1"
exec 3<"$FIXTURE1"
while IFS= read -r -u 3 line; do
    process_line "$line"
done
exec 3<&-
rm -f "$FIXTURE1"

[[ -s "$LOGFILE" ]] || fail "no JSONL written for backslash line"
LAST_LINE=$(tail -n1 "$LOGFILE")
python3 -c "import json,sys; json.loads(sys.argv[1])" "$LAST_LINE" \
    || fail "backslash line produced invalid JSON: $LAST_LINE"
python3 -c "
import json, sys
d = json.loads(sys.argv[1])
assert d['event'] == 'auth_401', d
assert '\\\\models' in d['raw'], d
" "$LAST_LINE" || fail "backslash not preserved correctly in raw field"
echo "PASS: backslash log line -> valid JSONL"

# --- Test 2: last-seen timestamp is persisted from a --timestamps-prefixed
#     line, so a reconnect resumes from there rather than a fixed window.
: > "$LOGFILE"
exec 3</dev/null  # no IP follow-up line queued; process_line must tolerate that
process_line '2026-09-25T00:05:30.123456789Z auth_exception_handler.py:42 - Exception occured - bad key'
exec 3<&-

STATEFILE="$WORK/.monitor-last-seen"
[[ -f "$STATEFILE" ]] || fail "no state file written"
SEEN=$(cat "$STATEFILE")
[[ "$SEEN" == "2026-09-25T00:05:30.123456789Z" ]] \
    || fail "state file has wrong last-seen ts: $SEEN"
echo "PASS: last-seen timestamp persisted for resume"

# --- Test 3: the false claim this ticket exists to remove must be gone.
if grep -q 'reconnects automatically' "$SCRIPT"; then
    fail "script still claims docker logs --follow reconnects automatically"
fi
echo "PASS: stale 'reconnects automatically' claim removed"

echo "ALL PASS"
