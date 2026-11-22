#!/usr/bin/env bash
# wq#2086 (Minerva proposal d653cc13c6a3) — install the presentation-stability probe.
#
# Two halves, deliberately:
#   * the probe script -> /home/lumen/bin/minerva-presentation-stability. Unprivileged,
#     idempotent, health-checked, auto-reverting. Inert on its own: nothing runs it.
#   * the units -> /etc/systemd/system. PRIVILEGED. Skipped with a printed command list
#     when this runs without write access there, so an unprivileged replay still leaves
#     the box in a consistent state (script live, timer absent, --verify says so).
#
# Change-safety order: verify (selftest) -> precheck -> backup -> apply -> health ->
# revert on fail.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN=/home/lumen/bin/minerva-presentation-stability
UNIT_DIR=/etc/systemd/system
UNITS="lumen-presentation-stability.service lumen-presentation-stability.timer"
BAK=/home/lumen/backups/wq2086

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/minerva_presentation_stability.py" "$BIN"
  vunit_file "$SRC/lumen-presentation-stability.service" "$UNIT_DIR/lumen-presentation-stability.service"
  vunit_file "$SRC/lumen-presentation-stability.timer"   "$UNIT_DIR/lumen-presentation-stability.timer"
  # wq#5258: no venabled/vactive/vtimer_scheduled for lumen-presentation-stability.timer:
  # intended-off, disabled by the 2026-10-08 subtraction
  # (/home/lumen/scratch/subtraction/timers-disable-list.txt:7, phase2-results.txt:7).
  # Asserting enabled+active made --verify fail permanently, and the ops-root-apply it filed
  # told the reader to run this script as root, which runs `systemctl enable --now` and
  # undoes the subtraction. The unit files are still checked above. Re-enabling the timer
  # is a decision, not a heal; whoever makes it adds venabled/vactive/vtimer_scheduled here.
  vdone
fi

# ---- verify + precheck: never touch a target before the source proves itself ----------
python3 "$SRC/minerva_presentation_stability.py" --selftest
[ -r /home/lumen/minerva/minerva_verify.py ] || {
  echo "precheck FAIL: /home/lumen/minerva/minerva_verify.py unreadable — the probe has "\
       "no gate to probe" >&2
  exit 1
}
[ -r /home/lumen/minerva/minerva.db ] || { echo "precheck FAIL: minerva.db unreadable" >&2; exit 1; }

# ---- backup ---------------------------------------------------------------------------
mkdir -p "$BAK"
had_bin=0
if [ -e "$BIN" ]; then
  had_bin=1
  cp -p "$BIN" "$BAK/minerva-presentation-stability.bak.$(date -u +%Y%m%dT%H%M%SZ)"
fi

# ---- apply (script half) --------------------------------------------------------------
"$SRC/../wq2856-atomic-install/install-script" -m 0755 "$SRC/minerva_presentation_stability.py" "$BIN"

# ---- health: the installed copy must pass its own invariants and read the live corpus --
if python3 "$BIN" --selftest && python3 "$BIN" --dry-run >/dev/null; then
  echo "wq2086 install: $BIN deployed and healthy"
else
  if [ "$had_bin" = 1 ]; then
    "$SRC/../wq2856-atomic-install/install-script" -m 0755 "$(ls -t "$BAK"/minerva-presentation-stability.bak.* | head -1)" "$BIN"
  else
    rm -f "$BIN"
  fi
  echo "wq2086 install: health FAILED — reverted" >&2
  exit 1
fi

# ---- apply (unit half, privileged) ----------------------------------------------------
if [ -w "$UNIT_DIR" ]; then
  for u in $UNITS; do
    [ -e "$UNIT_DIR/$u" ] && cp -p "$UNIT_DIR/$u" "$BAK/$u.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    cp "$SRC/$u" "$UNIT_DIR/$u"
    chmod 0644 "$UNIT_DIR/$u"
  done
  systemctl daemon-reload
  systemctl enable --now lumen-presentation-stability.timer
  echo "wq2086 install: timer enabled"
  echo "rollback: systemctl disable --now lumen-presentation-stability.timer && rm -f \\"
  echo "  $UNIT_DIR/lumen-presentation-stability.{service,timer} $BIN && systemctl daemon-reload"
else
  echo "wq2086 install: $UNIT_DIR not writable — units NOT installed. The probe is live"
  echo "  but nothing schedules it. Privileged steps still owed:"
  for u in $UNITS; do echo "    install -m 0644 $SRC/$u $UNIT_DIR/$u"; done
  echo "    systemctl daemon-reload"
  echo "    systemctl enable --now lumen-presentation-stability.timer"
  echo "  Confirm with: bash $SRC/install.sh --verify"
  exit 3
fi
