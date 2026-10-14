#!/usr/bin/env bash
# p1-update-gate.sh -- runs ON LUMEN. Read-only enforcement of control-plane
# rule 2 (claude_memory.md, 2026-09-25): "the backup is kept 2 weeks behind
# on updates, on its own day/time, and is the only machine updated in that
# window." This script NEVER touches p1-primary or p1-backup -- it only
# reads lumen_state.py and, when backup's update is due, opens an aq row +
# sends an ntfy alert so Frank (or an orchestrator, Frank driving) runs
# p1-update.sh on the backup by hand. Safe to run on an unattended timer
# (alert-only; control-plane rule 3 bans unattended CHANGES, not reads/alerts).
#
# Also catches the inverse mistake -- primary and backup updated inside the
# same window -- and alerts on that too, since rule 2 exists specifically so
# a bad update never takes down both nodes at once.
set -euo pipefail

STATE_PY=/home/lumen/minerva/lumen_state.py
WINDOW_DAYS=${P1_UPDATE_WINDOW_DAYS:-14}
NTFY_URL=${P1_GATE_NTFY_URL:-http://127.0.0.1:8080/lumen-fleet}

get_date() {
  # lumen_state.py current <subject> last_update -> prints "... = <value>  (since <date>) ..."
  # The authoritative date is the "(since YYYY-MM-DD)" stamp, not text inside <value>
  # (value is free-form, may itself start with a date string that drifts in format).
  python3 "$STATE_PY" current "$1" last_update 2>/dev/null | grep -oE '\(since [0-9]{4}-[0-9]{2}-[0-9]{2}\)' | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | tail -1
}

PRIMARY_DATE=$(get_date p1-primary || true)
BACKUP_DATE=$(get_date p1-backup || true)

if [ -z "$PRIMARY_DATE" ]; then
  echo "no p1-primary.last_update fact yet -- nothing to gate (pre-deployment or first update not yet recorded)"
  exit 0
fi

NOW_EPOCH=$(date -u +%s)
PRIMARY_EPOCH=$(date -u -d "$PRIMARY_DATE" +%s)
DAYS_SINCE_PRIMARY=$(( (NOW_EPOCH - PRIMARY_EPOCH) / 86400 ))

if [ -n "$BACKUP_DATE" ]; then
  BACKUP_EPOCH=$(date -u -d "$BACKUP_DATE" +%s)
  GAP_DAYS=$(( (BACKUP_EPOCH - PRIMARY_EPOCH) / 86400 ))
  ABS_GAP=${GAP_DAYS#-}
  if [ "$ABS_GAP" -lt "$WINDOW_DAYS" ]; then
    MSG="p1-primary updated ${PRIMARY_DATE}, p1-backup updated ${BACKUP_DATE} -- only ${ABS_GAP}d apart, rule 2 wants ${WINDOW_DAYS}d. Both nodes may have been touched in the same window."
    /home/lumen/bin/ntfy-send -p high -T "P1 update-window violation" "$MSG" 2>/dev/null || true
    curl -sf -m 5 -d "P1 update-window violation: ${MSG}" "$NTFY_URL" >/dev/null 2>&1 || true
    /home/lumen/bin/aq add p1-update-gate "p1-window-violation-${PRIMARY_DATE}" "P1 update-window violation" "$MSG" 2>/dev/null || true
    logger -t p1-update-gate "VIOLATION: $MSG"
    exit 0
  fi
fi

if [ -z "$BACKUP_DATE" ] || [ "$(date -u -d "$BACKUP_DATE" +%s)" -lt "$PRIMARY_EPOCH" ]; then
  if [ "$DAYS_SINCE_PRIMARY" -ge "$WINDOW_DAYS" ]; then
    MSG="p1-primary last updated ${PRIMARY_DATE} (${DAYS_SINCE_PRIMARY}d ago) -- p1-backup's ${WINDOW_DAYS}-day-behind update is due. Run p1-update.sh on p1-backup by hand (control-plane rule: Frank-driven, its own dedicated window, sole machine touched then)."
    /home/lumen/bin/ntfy-send -T "P1 backup update due" "$MSG" 2>/dev/null || true
    curl -sf -m 5 -d "P1 backup update due: ${MSG}" "$NTFY_URL" >/dev/null 2>&1 || true
    /home/lumen/bin/aq add p1-update-gate "p1-backup-due-${PRIMARY_DATE}" "P1 backup update due" "$MSG" 2>/dev/null || true
    logger -t p1-update-gate "DUE: $MSG"
  else
    echo "backup update not yet due: ${DAYS_SINCE_PRIMARY}/${WINDOW_DAYS} days since primary's last update (${PRIMARY_DATE})"
  fi
else
  echo "backup already updated on/after primary's last update (${BACKUP_DATE} >= ${PRIMARY_DATE}) -- nothing due"
fi
