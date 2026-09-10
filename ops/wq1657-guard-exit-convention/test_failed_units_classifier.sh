#!/usr/bin/env bash
# test_failed_units_classifier.sh (wq#1657) — offline harness for the
# finding-exit classifier in staged/lumen-failed-units. Stubs systemctl,
# journalctl, ntfy-send and aq; asserts a listed guard exiting 1 is reported
# "Guard fired" (low / guard-fired) while an unlisted unit stays
# "Failed unit" (high / failed-unit).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/state"

cat > "$T/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
cmd=$1; shift
case $cmd in
  list-units)
    echo "lumen-budget-alert.service loaded failed failed guard"
    echo "jay-watch.service loaded failed failed watcher" ;;
  show)
    case "$*" in
      *Result*)         echo exit-code ;;
      *ExecMainStatus*) echo 1 ;;
    esac ;;
  cat) exit 1 ;;   # no unit carries the tag -> allowlist path must decide
esac
EOF
cat > "$T/bin/journalctl" <<'EOF'
#!/usr/bin/env bash
echo "stub journal tail"
EOF
cat > "$T/bin/ntfy-send" <<'EOF'
#!/usr/bin/env bash
echo "ntfy $*" >> "$LOG"
EOF
cat > "$T/bin/aq" <<'EOF'
#!/usr/bin/env bash
echo "aq $*" >> "$LOG"
EOF
chmod +x "$T/bin/"*
export LOG="$T/calls.log"; : > "$LOG"

echo "lumen-budget-alert.service" > "$T/guard-finding-exit.list"

sed -e "s|^STATE_DIR=.*|STATE_DIR=$T/state|" \
    -e "s|^NTFY=.*|NTFY=$T/bin/ntfy-send|" \
    -e "s|^AQ=.*|AQ=$T/bin/aq|" \
    -e "s|^FINDING_LIST=.*|FINDING_LIST=$T/guard-finding-exit.list|" \
    "$HERE/staged/lumen-failed-units" > "$T/sweep"
chmod +x "$T/sweep"

PATH="$T/bin:$PATH" bash "$T/sweep" --print > "$T/stdout" 2>&1
rc=$?

fail=0
assert() { # $1=desc $2=pattern $3=file
  if grep -qE "$2" "$3"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}
assert "sweep exits 0"                 "^0$" <(echo "$rc")
assert "listed guard -> guard-fired aq row" \
       "^aq add guard-fired Guard fired: lumen-budget-alert.service" "$LOG"
assert "listed guard -> low-priority ntfy" \
       "^ntfy .*-p low .*Guard fired: lumen-budget-alert.service" "$LOG"
assert "unlisted unit -> failed-unit aq row" \
       "^aq add failed-unit Failed unit: jay-watch.service" "$LOG"
assert "unlisted unit -> high-priority ntfy" \
       "^ntfy .*-p high .*Failed unit: jay-watch.service" "$LOG"
if grep -qE "guard-fired.*jay-watch|failed-unit Failed unit: lumen-budget-alert" "$LOG"; then
  echo "FAIL cross-classification"; fail=1
else
  echo "ok   no cross-classification"
fi

[[ $fail == 0 ]] && echo "ALL PASS" || echo "FAILURES (log: $T/calls.log)"
exit $fail
