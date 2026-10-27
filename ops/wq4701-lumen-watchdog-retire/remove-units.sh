#!/usr/bin/env bash
# wq#4701 — remove the retired lumen-watchdog timer/service and move the script
# aside. The timer has been disabled since 2026-08-27 (wq#970, Gatus took over
# the device/service checks); this finishes that retirement.
#
# Requires root (the unit files are root:root under /etc/systemd/system) and a
# caller not bound by the build-lane deny-list on systemctl stop/disable/mask
# (foreman/harness/guard.sh). Run as:
#
#   sudo bash ops/wq4701-lumen-watchdog-retire/remove-units.sh
#
# Evidence and decision: README.md (this directory), docs/fleet/retired_services.yaml
# (name: lumen-watchdog). Rollback: README.md "Rollback". The unit files are
# preserved verbatim in units/ in this directory.
set -euo pipefail

UNITS=(lumen-watchdog.timer lumen-watchdog.service)
DROPIN_DIR=/etc/systemd/system/lumen-watchdog.service.d
STAMP=/home/lumen/logs/.stamp.lumen-watchdog
SCRIPT=/home/lumen/agent/lumen_watchdog.py
SCRIPT_RETIRED=/home/lumen/agent/lumen_watchdog.py.retired-wq4701
MARKER=/home/lumen/agent/lumen_watchdog.RETIRED.txt

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root (writing /etc/systemd/system)" >&2
  exit 1
fi

echo "== before =="
for u in "${UNITS[@]}"; do
  systemctl show "$u" -p LoadState -p ActiveState -p UnitFileState | tr '\n' ' '
  echo "<- $u"
done

systemctl disable --now lumen-watchdog.timer 2>/dev/null || true
rm -f /etc/systemd/system/lumen-watchdog.timer \
      /etc/systemd/system/lumen-watchdog.service
rm -rf "$DROPIN_DIR"
systemctl daemon-reload
systemctl reset-failed lumen-watchdog.timer lumen-watchdog.service 2>/dev/null || true

echo "== after (expect 'could not be found' for both) =="
rc=0
for u in "${UNITS[@]}"; do
  if systemctl status "$u" 2>&1 | grep -q "could not be found"; then
    echo "OK removed: $u"
  else
    echo "STILL PRESENT: $u" >&2
    rc=1
  fi
done

# Move the script aside rather than delete it: rollback is a rename, and the
# .retired-wq4701 suffix keeps it out of anything that globs *.py.
if [ -e "$SCRIPT" ]; then
  sudo -u lumen mv "$SCRIPT" "$SCRIPT_RETIRED"
  echo "moved $SCRIPT -> $SCRIPT_RETIRED"
fi
sudo -u lumen tee "$MARKER" >/dev/null <<'TXT'
# RETIRED 2026-08-27 wq#970: device/service coverage backfilled into Gatus. HOMELAB_STATUS.json no longer updates; query Gatus :8418 instead.
# REMOVED wq#4701: units deleted, script moved to lumen_watchdog.py.retired-wq4701. Rollback: foreman ops/wq4701-lumen-watchdog-retire/README.md "Rollback".
TXT

# The stamp only exists because of the manual wq#4695 acceptance run on
# 2026-10-27. With no timer, cron_deadman.py derives no cadence for this unit and
# skips it, so this is tidy-up, not alert suppression.
rm -f "$STAMP" || true

# Register both units as deliberately removed (wq#1041) so a reappearance at
# top-level reads as a respawn rather than as a fresh install. Idempotent.
KNOWN_RM=/home/lumen/fleet-units/known-removed.txt
if [ "$rc" -eq 0 ]; then
  for u in "${UNITS[@]}"; do
    grep -q "^$u[[:space:]]" "$KNOWN_RM" || grep -qxF "$u" "$KNOWN_RM" \
      || printf '%s\t%s\twq#4701 removed (units preserved in foreman ops/wq4701-lumen-watchdog-retire/units)\n' \
           "$u" "$(date -u +%F)" | sudo -u lumen tee -a "$KNOWN_RM" >/dev/null
  done
fi

# Fold the removal into the /home/lumen/fleet-units baseline, otherwise
# unit-drift check flags the disappearance as unexpected drift next run.
sudo -u lumen /home/lumen/bin/unit-drift snapshot
sudo -u lumen /home/lumen/bin/unit-drift check --digest

exit "$rc"
