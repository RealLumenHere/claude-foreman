#!/usr/bin/env bash
# test_path_unit_urgent.sh (wq#1655) — offline harness for staged/lumen-failed-units.
# Stubs systemctl/journalctl/ntfy-send/aq; asserts (a) the systemctl list-units
# call now requests --type=path (the sweep used to omit .path units entirely,
# so fleet-inventory-render.path's 2026-09-10 05:39:45Z failure went
# unmonitored), and (b) a newly-failed .path unit is reported at "urgent"
# priority, distinct from an ordinary .service failure at "high".
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/state"

grep -q -- '--type=path' "$HERE/staged/lumen-failed-units"
echo $([[ $? -eq 0 ]] && echo ok || echo FAIL) "sweep's systemctl list-units includes --type=path"

cat > "$T/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
cmd=$1; shift
case $cmd in
  list-units)
    echo "fleet-inventory-render.path loaded failed failed re-render on inventory change"
    echo "jay-watch.service loaded failed failed watcher" ;;
  show)
    case "$*" in
      *Result*)         echo start-limit-hit ;;
      *ExecMainStatus*) echo 0 ;;
    esac ;;
  cat) exit 1 ;;
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

sed -e "s|^STATE_DIR=.*|STATE_DIR=$T/state|" \
    -e "s|^NTFY=.*|NTFY=$T/bin/ntfy-send|" \
    -e "s|^AQ=.*|AQ=$T/bin/aq|" \
    -e "s|^FINDING_LIST=.*|FINDING_LIST=$T/no-such-list|" \
    "$HERE/staged/lumen-failed-units" > "$T/sweep"
chmod +x "$T/sweep"

PATH="$T/bin:$PATH" bash "$T/sweep" --print > "$T/stdout" 2>&1
rc=$?

fail=0
assert() { # $1=desc $2=pattern $3=file
  if grep -qE "$2" "$3"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}
assert "sweep exits 0" "^0$" <(echo "$rc")
assert "path unit -> urgent-priority ntfy" \
       "^ntfy .*-p urgent .*Failed unit: fleet-inventory-render\.path" "$LOG"
assert "path unit message flags non-recovery" \
       "NEVER SELF-RECOVERS.*reset-failed fleet-inventory-render\.path" "$LOG"
assert "ordinary service -> high-priority ntfy" \
       "^ntfy .*-p high .*Failed unit: jay-watch\.service" "$LOG"
if grep -qE "urgent.*jay-watch" "$LOG"; then
  echo "FAIL cross-classification (jay-watch marked urgent)"; fail=1
else
  echo "ok   no cross-classification"
fi

[[ $fail == 0 ]] && echo "ALL PASS" || echo "FAILURES (log: $T/calls.log)"
exit $fail
