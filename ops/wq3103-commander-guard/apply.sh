#!/bin/bash
# wq#3103 apply: Commander :3100 loopback bind + Cloudflare Access JWT guard, with a runtime shadow/enforce switch.
# CONTROL-PLANE RULE (c): Frank driving. Commander and cloudflared ride this unit, so ALWAYS run detached:
#   ssh lumen@100.117.66.102 'sudo systemd-run --unit=dc-guard-apply --collect bash <this dir>/apply.sh deploy'
#   then: journalctl -u dc-guard-apply -f     (or tail /home/lumen/logs/wq3103-apply-*.log)
#
#   deploy    install preload + drop-in in SHADOW mode, restart the unit ONCE, verify, keep a dead-man rollback armed
#             until local health AND real tunnel traffic through the guard are proven. Any failure = auto rollback.
#   flip      shadow -> enforce, NO restart (mode file). Needs >=1 valid and 0 denied tunnel verdicts since deploy, then
#             needs a fresh valid tunnel verdict after the flip, else flips itself back to shadow.
#   unflip    enforce -> shadow (no restart).
#   rollback  remove the drop-in + mode file, restart the unit.  status   read-only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); OPS=$(dirname "$D")
UNIT=${UNIT:-desktop-commander-mcp}; PORT=${PORT:-3100}; BIN=${BIN:-/home/lumen/bin}
MODE_FILE=${MODE_FILE:-/etc/lumen/commander-guard.mode}
AUD=${AUD:-13c7d7d25d29d305f9c0441cc95b47c16d6ab93a9aa5ff89995fb66acf166663}   # mcp-backend.lumennode.uk Access app (public cf-access-aud)
TEAM=${TEAM:-lumenseyes}
DROPDIR=/etc/systemd/system/$UNIT.service.d; DROP=$DROPDIR/20-wq3103-commander-guard.conf
DEADMAN_SEC=${DEADMAN_SEC:-420}; EVIDENCE_WAIT=${EVIDENCE_WAIT:-240}; FLIP_WAIT=${FLIP_WAIT:-150}
SKIP_TUNNEL_EVIDENCE=${SKIP_TUNNEL_EVIDENCE:-0}     # tests only
TS=$(date +%Y%m%d_%H%M%S); LOGDIR=${LOGDIR:-/home/lumen/logs}; mkdir -p "$LOGDIR"; LOG=$LOGDIR/wq3103-apply-$TS.log
exec > >(tee -a "$LOG") 2>&1
[ "$(id -u)" = 0 ] || { echo "needs root"; exit 9; }
say() { echo "[$(date +%H:%M:%S)] $*"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$@"; }
anon_init() { code -X POST "http://127.0.0.1:$PORT/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"apply","version":"1"}}}'; }
wait_health() { for _ in $(seq 1 60); do [ "$(code "http://127.0.0.1:$PORT/health")" = 200 ] && return 0; sleep 0.5; done; return 1; }
listen_addrs() { ss -tlnH "sport = :$PORT" | awk '{print $4}' | paste -sd,; }
loopback_only() { local a; a=$(listen_addrs); [ -n "$a" ] && ! tr ',' '\n' <<<"$a" | grep -v '^127\.0\.0\.1:' | grep -q .; }
verdicts() {  # $1 = epoch since, $2 = optional mode filter (shadow|enforce); prints "<tunnel_ok> <tunnel_deny>"
  # journal --since has 1s granularity, so a post-flip check must filter on the mode the guard logged, not on time alone
  local l; l=$(journalctl -u "$UNIT" --since "@$1" --no-pager -o cat 2>/dev/null | grep '"guard":"access"' | grep '"event":"verdict"' | grep '"via":"tunnel"' || true)
  [ -n "${2:-}" ] && l=$(grep "\"mode\":\"$2\"" <<<"$l" || true)
  echo "$(grep -c '"ok":true' <<<"$l" || true) $(grep -c '"ok":false' <<<"$l" || true)"; }
mode() { cat "$MODE_FILE" 2>/dev/null || echo none; }
deployed() { [ -f "$DROP" ]; }

do_rollback() {
  say "ROLLBACK: removing drop-in + mode file, restarting $UNIT"
  rm -f "$DROP" "$MODE_FILE"; systemctl daemon-reload; systemctl restart "$UNIT"
  if wait_health; then say "rollback: $UNIT healthy, listen=$(listen_addrs), anon_mcp=$(anon_init)"; else say "!! rollback: $UNIT NOT healthy - investigate NOW (journalctl -u $UNIT)"; fi
}
disarm() { systemctl stop "$UNIT-guard-rollback.timer" 2>/dev/null || true; }
fail_deploy() { say "!! $*"; disarm; do_rollback; exit 1; }

case "${1:-}" in
deploy)
  deployed && { say "already deployed ($DROP exists); use status/flip/rollback"; exit 2; }
  say "== preflight: offline tests (scratch ports, touches nothing live)"
  for t in "$D/test_commander_guard.mjs" "$D/test_call_window.mjs" "$OPS/wq2768-access-guard/test_access_guard.mjs"; do
    runuser -u lumen -- env HOME=/home/lumen node "$t" | tail -1 | grep -qE '^([0-9]+)/\1 passed$' || { say "offline test FAILED: $t. NOT deploying"; exit 1; }
  done
  say "== baseline"; b_health=$(code "http://127.0.0.1:$PORT/health"); b_anon=$(anon_init); say "health=$b_health anon_mcp=$b_anon listen=$(listen_addrs)"
  [ "$b_health" = 200 ] || { say "unit not healthy before change, abort"; exit 2; }
  B=/home/lumen/backups/wq3103_$TS; mkdir -p "$B"; systemctl cat "$UNIT" > "$B/unit.cat" 2>&1; cp -a "$DROPDIR" "$B/dropins.orig" 2>/dev/null || true
  cp -a "$BIN/access-jwt-guard.cjs" "$B/" 2>/dev/null || true; cp -a "$D/apply.sh" "$B/apply.sh"
  say "== arm dead-man (${DEADMAN_SEC}s) BEFORE any change: runs 'rollback' from the backup copy of this script"
  systemd-run --unit="$UNIT-guard-rollback" --on-active="$DEADMAN_SEC" --collect --setenv=UNIT="$UNIT" --setenv=PORT="$PORT" --setenv=MODE_FILE="$MODE_FILE" \
    --setenv=BIN="$BIN" --setenv=LOGDIR="$LOGDIR" bash "$B/apply.sh" rollback >/dev/null || { say "cannot arm dead-man, abort"; exit 2; }
  [ "${TEST_DIE_AFTER_ARM:-0}" = 1 ] && { say "TEST HOOK: dying after arm"; kill -9 $$; }
  say "== install"
  install -o lumen -g lumen -m 0644 "$OPS/wq2768-access-guard/access-jwt-guard.cjs" "$BIN/access-jwt-guard.cjs"
  install -o lumen -g lumen -m 0644 "$OPS/wq2751-listener-audit/loopback-listen.cjs" "$BIN/loopback-listen.cjs"
  install -o lumen -g lumen -m 0644 "$D/dc-call-window.cjs" "$BIN/dc-call-window.cjs"
  install -o lumen -g lumen -m 0644 "$D/commander-preload.cjs" "$BIN/commander-preload.cjs"
  mkdir -p "$(dirname "$MODE_FILE")"; echo shadow > "$MODE_FILE"; chmod 0644 "$MODE_FILE"; chown root:root "$MODE_FILE"
  mkdir -p "$DROPDIR"; {
    echo "# wq#3103: Commander loopback bind + Access JWT guard. Mode lives in $MODE_FILE (shadow|enforce), flip without restart."
    echo "# Never set ACCESS_GUARD_ISS / ACCESS_GUARD_CERTS_URL here (test-only overrides)."
    echo "[Service]"
    echo "Environment=NODE_OPTIONS=--require=$BIN/commander-preload.cjs"
    echo "Environment=LOOPBACK_LISTEN_PORT=$PORT"
    echo "Environment=ACCESS_GUARD_PORT=$PORT"
    echo "Environment=ACCESS_GUARD_TEAM=$TEAM"
    echo "Environment=ACCESS_GUARD_AUD=$AUD"
    echo "Environment=ACCESS_GUARD_MODE=shadow"
    echo "Environment=ACCESS_GUARD_MODE_FILE=$MODE_FILE"
    echo "RestartPreventExitStatus=78"
    [ -n "${EXTRA_DROPIN_LINES:-}" ] && printf '%b\n' "$EXTRA_DROPIN_LINES"
  } > "$DROP"; chmod 0644 "$DROP"
  T0=$(date +%s); systemctl daemon-reload
  say "== restart $UNIT (the one restart; sessions on this unit drop and re-init)"; systemctl restart "$UNIT"
  wait_health || fail_deploy "no /health after restart"
  say "health ok, listen=$(listen_addrs), mode=$(mode)"
  loopback_only || fail_deploy "port $PORT not loopback-only: $(listen_addrs)"
  a=$(anon_init); [ "${a:0:1}" = 2 ] || fail_deploy "shadow mode should pass anonymous initialize, got $a"
  # the guard fetches the Access signing certs asynchronously after the port is up: poll, do not sample once
  certs=0; for _ in $(seq 1 30); do journalctl -u "$UNIT" --since "@$T0" --no-pager -o cat | grep -q '"guard":"access".*"event":"certs"' && { certs=1; break; }; sleep 1; done
  [ "$certs" = 1 ] || fail_deploy "guard did not load Access certs within 30s (not active, or Access certs endpoint unreachable)"
  say "guard active: certs loaded, anon_mcp=$a (shadow), bound $(listen_addrs)"
  if [ "$SKIP_TUNNEL_EVIDENCE" = 1 ]; then say "TEST: skipping tunnel evidence"; else
    say "== waiting up to ${EVIDENCE_WAIT}s for real tunnel traffic through the guard (mcpw cron probe / any Commander call)"
    seen=0; for _ in $(seq 1 "$EVIDENCE_WAIT"); do read -r ok deny <<<"$(verdicts "$T0")"; [ $((ok+deny)) -ge 1 ] && { seen=1; break; }; sleep 1; done
    [ "$seen" = 1 ] || fail_deploy "no tunnel request reached the guard in ${EVIDENCE_WAIT}s (cloudflared -> loopback path broken?)"
    say "tunnel traffic seen: valid=$ok would_deny=$deny"
  fi
  disarm; say "DONE deploy ok (SHADOW). dead-man disarmed. Next: watch 'status' until valid>=1 and would_deny=0, then: apply.sh flip. log=$LOG backup=$B" ;;
flip)
  deployed || { say "not deployed"; exit 2; }; [ "$(mode)" = shadow ] || { say "mode is $(mode), nothing to flip"; exit 0; }
  since=$(stat -c %Y "$DROP"); read -r ok deny <<<"$(verdicts "$since")"; say "tunnel verdicts since deploy: valid=$ok would_deny=$deny"
  if [ "$ok" -lt 1 ] || [ "$deny" -ne 0 ]; then say "NO FLIP: need >=1 valid and 0 denied tunnel verdicts (a denied one means the pinned AUD/ISS is wrong)"; exit 3; fi
  say "safety: revert-to-shadow armed in $((FLIP_WAIT+40))s (file write, no restart)"
  systemd-run --unit="$UNIT-guard-flipback" --on-active=$((FLIP_WAIT+40)) --collect bash -c "echo shadow > '$MODE_FILE'" >/dev/null
  cp -p "$MODE_FILE" "$MODE_FILE.bak.$TS"; echo enforce > "$MODE_FILE"; T1=$(date +%s); sleep 2
  a=$(anon_init); h=$(code "http://127.0.0.1:$PORT/health"); say "enforce: anon_mcp=$a (want 403) health=$h (want 200)"
  if [ "$a" != 403 ] || [ "$h" != 200 ]; then echo shadow > "$MODE_FILE"; systemctl stop "$UNIT-guard-flipback.timer" 2>/dev/null; say "!! reverted to shadow"; exit 4; fi
  say "waiting up to ${FLIP_WAIT}s for a fresh valid tunnel verdict in ENFORCE (proves the mcpw path still passes)"
  good=0; for _ in $(seq 1 "$FLIP_WAIT"); do read -r ok deny <<<"$(verdicts "$T1" enforce)"; [ "$ok" -ge 1 ] && { good=1; break; }; sleep 1; done
  if [ "$good" != 1 ]; then echo shadow > "$MODE_FILE"; systemctl stop "$UNIT-guard-flipback.timer" 2>/dev/null; say "!! no valid tunnel request after flip -> reverted to shadow (deny=$deny)"; exit 4; fi
  systemctl stop "$UNIT-guard-flipback.timer" 2>/dev/null; say "DONE enforce. tunnel valid=$ok since flip. anonymous loopback initialize now 403." ;;
unflip) echo shadow > "$MODE_FILE"; say "mode -> shadow (no restart)" ;;
rollback) do_rollback ;;
status)
  say "deployed=$(deployed && echo yes || echo no) mode=$(mode) listen=$(listen_addrs) health=$(code "http://127.0.0.1:$PORT/health") anon_mcp=$(anon_init)"
  if deployed; then read -r ok deny <<<"$(verdicts "$(stat -c %Y "$DROP")")"; say "tunnel verdicts since deploy: valid=$ok would_deny=$deny"; fi ;;
*) echo "usage: $0 deploy|flip|unflip|rollback|status" >&2; exit 2 ;;
esac
