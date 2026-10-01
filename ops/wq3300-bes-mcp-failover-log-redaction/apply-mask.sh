#!/usr/bin/env bash
# wq#3300 apply (part 2 of 2) — mask the body/chatter stream in-flight, port
# of wq#2546 (ops/wq1698-supergateway-log-redaction/wq2546-mask-body-log.conf)
# to Bes. Run FROM LUMEN, after apply.sh. Same sandbox posture as part 1: a
# proposal for an operator/session with ssh+sudo access to Bes, not this
# worker.
#
# Unlike wq#2546's drop-in (which hardcodes desktop-commander-mcp's exact
# ExecStart as a literal), this script DERIVES bes-mcp-failover's current
# ExecStart from the live unit via `systemctl show -p ExecStart` instead of
# assuming it — this worker's sandbox has no ssh/sudo to Bes to read that
# line and verify a hardcoded guess, and the doctrine is "verify, don't
# guess" (CLAUDE.md). The wrapper only changes stdout's destination
# (through mask-secrets) — it reconstructs the live ExecStart verbatim, so
# this stays a pure masking change, not a behavior change to flags/ports.
set -euo pipefail
BES=bes@100.110.156.91
OPS=/home/lumen/foreman/ops/wq3300-bes-mcp-failover-log-redaction
DROPIN_DIR=/etc/systemd/system/bes-mcp-failover.service.d
LOGFILE=/home/bes/logs/supergateway/bes-mcp-failover.log
b() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$BES" "$@"; }
fail() { echo "CHECK FAILED: $*"; exit 1; }

b "[ -f $DROPIN_DIR/no-body-logs-journald.conf ]" || fail "wq#3300 part 1 (no-body-logs-journald.conf) is not installed yet — run apply.sh first."

# Copy mask-secrets onto Bes (self-contained bash, no deps beyond sed -E —
# see ops/wq1698-supergateway-log-redaction/README.md's lockstep note).
scp -o BatchMode=yes /home/lumen/bin/mask-secrets "$BES:/tmp/mask-secrets" || fail "scp mask-secrets failed"
b "sudo -n install -D -m 0755 -o root -g root /tmp/mask-secrets /usr/local/bin/mask-secrets" || fail "install mask-secrets failed"

# Pull the EXACT live ExecStart (ExecStart= strips systemd's leading
# '{ path=...}' annotation down to the raw command) rather than assuming it.
execstart=$(b "systemctl show -p ExecStart bes-mcp-failover.service" | sed -n 's/^ExecStart={ path=\([^ ]*\) ; argv\[\]=\(.*\) ; ignore_errors=.*}$/\2/p')
[ -n "$execstart" ] || fail "could not parse live ExecStart from systemctl show — refusing to guess it"
echo "live ExecStart argv: $execstart"

tmp=$(mktemp)
{
  echo "# Installed by wq#3300 apply-mask.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)."
  echo "# ExecStart below is the live command this script read from"
  echo "# 'systemctl show -p ExecStart bes-mcp-failover.service' at install time,"
  echo "# wrapped so only stdout (body/chatter) passes through mask-secrets."
  echo "# stderr is untouched (still governed by no-body-logs-journald.conf's"
  echo "# StandardError=journal)."
  echo "[Service]"
  echo "StandardOutput=journal"
  echo "ExecStart="
  echo "ExecStart=/bin/bash -c 'exec $execstart > >(exec /usr/local/bin/mask-secrets >> $LOGFILE)'"
} > "$tmp"

scp -o BatchMode=yes "$tmp" "$BES:/tmp/wq3300-mask-body-log.conf" || fail "scp drop-in failed"
rm -f "$tmp"
b "sudo -n install -D -m 0644 /tmp/wq3300-mask-body-log.conf $DROPIN_DIR/wq3300-mask-body-log.conf" || fail "install drop-in failed"

b "sudo -n systemctl daemon-reload" || fail "daemon-reload failed"
b "sudo -n systemd-run --collect --unit=wq3300-restart-mcp-failover-mask systemctl restart bes-mcp-failover" || fail "restart dispatch failed"
for i in $(seq 1 15); do
  sleep 1
  b "systemctl is-active --quiet bes-mcp-failover" && break
done
b "systemctl is-active --quiet bes-mcp-failover" || fail "bes-mcp-failover not active after restart"

sleep 2
hc=$(b "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health" || echo 000)
echo "post-apply health=$hc"
[ "$hc" = 200 ] || fail "HEALTH CHECK FAILED — roll back: rm $DROPIN_DIR/wq3300-mask-body-log.conf, daemon-reload, restart"

echo "APPLIED. $LOGFILE now receives masked body/chatter only."
echo "Verify: ssh $BES 'tail -n 20 $LOGFILE' | /home/lumen/bin/mask-secrets  # should show MASKED, never a raw value"
echo "Next: journalctl --vacuum-size=200M on Bes (reclaim the 3.9G journal), then rotate the fleet sudo password (pending since 07-11)."
