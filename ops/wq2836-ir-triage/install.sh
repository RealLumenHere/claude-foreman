#!/usr/bin/env bash
# wq#2836 — install lumen-ir-triage. Run as root via `lumen-run --root` on
# lumen itself (repo convention — see ops/wq1926-kernel-watch).
#
# Read-only, proposal-only: the pass writes nothing but a minerva proposal row
# (status=blocked, human review only) and its markdown twin.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/ir_triage.py"            /home/lumen/minerva/ir_triage.py
  vunit_file "$SRC/lumen-ir-triage.service" /etc/systemd/system/lumen-ir-triage.service
  vunit_file "$SRC/lumen-ir-triage.timer"   /etc/systemd/system/lumen-ir-triage.timer
  venabled   lumen-ir-triage.timer
  vactive    lumen-ir-triage.timer
  vdone
fi

echo "== script -> /home/lumen/minerva/ir_triage.py =="
install -o lumen -g lumen -m 755 "$SRC/ir_triage.py" /home/lumen/minerva/ir_triage.py

echo "== systemd units =="
install -m 644 "$SRC/lumen-ir-triage.service" /etc/systemd/system/lumen-ir-triage.service
install -m 644 "$SRC/lumen-ir-triage.timer" /etc/systemd/system/lumen-ir-triage.timer
systemctl daemon-reload

echo "== enable =="
systemctl enable --now lumen-ir-triage.timer
systemctl list-timers lumen-ir-triage.timer --no-pager || true

echo "== smoke: one pass now rather than waiting for the first timer fire =="
systemctl start lumen-ir-triage.service || true
journalctl -u lumen-ir-triage.service -n 15 --no-pager || true
