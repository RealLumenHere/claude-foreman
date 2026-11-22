#!/usr/bin/env bash
# wq#2836 — install lumen-ir-triage. Run as root via `lumen-run --root` on
# lumen itself (repo convention — see ops/wq1926-kernel-watch).
#
# Read-only, proposal-only: the pass writes nothing but a minerva proposal row
# (status=blocked, human review only) and its markdown twin.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
# wq#5267: 0 = intended-off (disabled by the 2026-10-08 subtraction,
# /home/lumen/scratch/subtraction/timers-disable-list.txt:2). A deliberate re-enable flips
# this to 1, which makes install enable+start the timer (plus a smoke pass) and --verify
# assert it enabled, active and scheduled. Not an env override: the intent lives here.
IR_TRIAGE_ON=0

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/ir_triage.py"            /home/lumen/minerva/ir_triage.py
  vunit_file "$SRC/lumen-ir-triage.service" /etc/systemd/system/lumen-ir-triage.service
  vunit_file "$SRC/lumen-ir-triage.timer"   /etc/systemd/system/lumen-ir-triage.timer
  # wq#5267: lumen-ir-triage.timer is intended-off (2026-10-08 subtraction). wq#4910
  # dropped vtimer_scheduled for that reason but left venabled/vactive, so --verify failed
  # forever and filed an ops-root-apply telling the reader to run this script as root,
  # whose `enable --now` would have undone the subtraction (same class as wq#5259/wq#5263).
  # The unit files are still verified; the timer's state is not asserted. Re-enabling it is
  # a decision, not a heal: whoever makes it sets IR_TRIAGE_ON=1 above.
  if [ "$IR_TRIAGE_ON" = 1 ]; then
    venabled         lumen-ir-triage.timer
    vactive          lumen-ir-triage.timer
    vtimer_scheduled lumen-ir-triage.timer
  fi
  vdone
fi

echo "== script -> /home/lumen/minerva/ir_triage.py =="
install -o lumen -g lumen -m 755 "$SRC/ir_triage.py" /home/lumen/minerva/ir_triage.py

echo "== systemd units =="
install -m 644 "$SRC/lumen-ir-triage.service" /etc/systemd/system/lumen-ir-triage.service
install -m 644 "$SRC/lumen-ir-triage.timer" /etc/systemd/system/lumen-ir-triage.timer
systemctl daemon-reload

if [ "$IR_TRIAGE_ON" = 1 ]; then
  echo "== enable =="
  systemctl enable --now lumen-ir-triage.timer
  systemctl list-timers lumen-ir-triage.timer --no-pager || true

  echo "== smoke: one pass now rather than waiting for the first timer fire =="
  systemctl start lumen-ir-triage.service || true
  journalctl -u lumen-ir-triage.service -n 15 --no-pager || true
else
  # wq#5267: intended-off; installing files must not undo the 2026-10-08 subtraction,
  # and a smoke pass would spend LLM budget on a lane that is switched off.
  echo "lumen-ir-triage units installed; timer left as-is (intended-off, IR_TRIAGE_ON=0)"
fi
