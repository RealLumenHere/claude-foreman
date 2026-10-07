#!/usr/bin/env bash
# wq#3522 loopback smoke test (ticket requirement: "test with a loopback pty
# pair via socat before real hardware"). Proves ser2net.yaml.tmpl's syntax is
# valid and that its TCP accepter binds ONLY the address it's given -- never a
# wildcard -- using a fake pty pair instead of a real DUT. Safe to run anywhere
# with ser2net and socat installed; never touches /etc/ser2net.yaml or the
# packaged ser2net.service. install.sh runs this before ever enabling the real
# unit (per-ticket gate, not optional).
#
# Usage: test_loopback.sh <path-to-ser2net.yaml.tmpl>
set -euo pipefail
TMPL="${1:?usage: test_loopback.sh <ser2net.yaml.tmpl>}"
[ -r "$TMPL" ] || { echo "FAIL: template unreadable: $TMPL"; exit 1; }

WORK=$(mktemp -d)
PORT=27001   # test-only, ephemeral; the real deploy picks its own port per DUT
SOCAT_PID=""
SER2NET_PID=""
cleanup() {
  [ -n "$SER2NET_PID" ] && kill "$SER2NET_PID" 2>/dev/null || true
  [ -n "$SOCAT_PID" ] && kill "$SOCAT_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# 1. Fabricate a loopback pty pair standing in for a real DUT UART. Canonical
#    socat PTY address syntax per socat(1): "PTY,link=PATH,raw,echo=0".
socat -d -d "PTY,link=$WORK/dut,raw,echo=0" "PTY,link=$WORK/tester,raw,echo=0" \
  >"$WORK/socat.log" 2>&1 &
SOCAT_PID=$!
up=""
for _ in $(seq 1 50); do
  if [ -e "$WORK/dut" ] && [ -e "$WORK/tester" ]; then up=1; break; fi
  sleep 0.1
done
[ -n "$up" ] || { echo "FAIL: socat pty pair never appeared"; cat "$WORK/socat.log"; exit 1; }

# 2. Render a test-only config: same address-bound accepter shape as the real
#    template, pointed at the fake pty instead of a real tty, on a throwaway
#    loopback-only port.
TESTCONF="$WORK/ser2net-test.yaml"
sed -e "s/__TAILNET_IP__/127.0.0.1/" \
    -e "s#/dev/ttyUSB0#$WORK/dut#" \
    -e "s/,7001$/,$PORT/" \
    "$TMPL" > "$TESTCONF"

# 3. Run ser2net in the foreground against the test config.
ser2net -c "$TESTCONF" -n -d >"$WORK/ser2net.log" 2>&1 &
SER2NET_PID=$!
up=""
for _ in $(seq 1 50); do
  if ss -tln 2>/dev/null | grep -q ":$PORT "; then up=1; break; fi
  sleep 0.1
done
[ -n "$up" ] || { echo "FAIL: ser2net never opened the accepter port"; cat "$WORK/ser2net.log"; exit 1; }

# 4. The accepter must be loopback-only -- no wildcard bind, ever.
LISTEN="$(ss -tln 2>/dev/null | grep ":$PORT " || true)"
if printf '%s\n' "$LISTEN" | grep -qE '(^|[^0-9.])0\.0\.0\.0:'"$PORT"'|(^|[^0-9a-fA-F:])\*:'"$PORT"'|:::'"$PORT"; then
  echo "FAIL: accepter is listening on a wildcard address: $LISTEN"
  exit 1
fi
printf '%s\n' "$LISTEN" | grep -q "127.0.0.1:$PORT" || {
  echo "FAIL: accepter never came up on 127.0.0.1:$PORT (got: $LISTEN)"; exit 1
}

# 5. Prove bytes actually cross dut-pty -> ser2net -> TCP client.
printf 'BOOT-LOG-OK\n' > "$WORK/tester"
sleep 0.3
got="$(timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT; cat <&3" 2>/dev/null || true)"
case "$got" in
  *BOOT-LOG-OK*)
    echo "PASS: loopback bytes observed over TCP, accepter bound to 127.0.0.1:$PORT only"
    ;;
  *)
    echo "FAIL: did not observe loopback bytes over TCP (got: '$got')"
    exit 1
    ;;
esac
