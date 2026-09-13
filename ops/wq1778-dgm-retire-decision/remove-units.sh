#!/usr/bin/env bash
# wq#1779 — retire the lumen-dgm timer/service (apply step of wq#1778).
#
# Requires root (both unit files are root:root under /etc/systemd/system) and a
# caller not bound by the build-lane deny-list on systemctl stop/disable/mask
# (foreman/harness/guard.sh:92-93, worker-settings.json:107-109). Run as:
#
#   sudo bash ops/wq1778-dgm-retire-decision/remove-units.sh
#
# Rationale and evidence: RUNBOOK.md (this directory);
# docs/fleet/retired_services.yaml (name: lumen-dgm).
# Rollback: RUNBOOK.md "Rollback" — move both files back out of the archive,
# daemon-reload, `systemctl enable --now lumen-dgm.timer`.
#
# Archive (not rm): the wq#1112 convention for retired units, and it keeps
# rollback to a `mv` back. /home/lumen/autonomy/agent5/ is left untouched.
set -euo pipefail

ARCHIVE=/etc/systemd/system/DISABLED-lumen-dgm.bak
UNITS=(lumen-dgm.timer lumen-dgm.service)

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root (writing /etc/systemd/system)" >&2
  exit 1
fi

echo "== before =="
for u in "${UNITS[@]}"; do
  systemctl show "$u" -p LoadState -p ActiveState -p UnitFileState | tr '\n' ' '
  echo "<- $u"
done

# The timer is the service's only trigger (TriggeredBy/RequiredBy verified
# 2026-09-13), so disabling it is what stops the daily 17:00 UTC cycle. The
# service itself is UnitFileState=static — it has no [Install] section, so
# `systemctl disable lumen-dgm.service` is a no-op; archiving its file is what
# removes it. Do not add a disable call for it.
systemctl disable --now lumen-dgm.timer

mkdir -p "$ARCHIVE"
for u in "${UNITS[@]}"; do
  mv "/etc/systemd/system/$u" "$ARCHIVE/$u"
done
systemctl daemon-reload
systemctl reset-failed "${UNITS[@]}" 2>/dev/null || true

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

if [ "$(systemctl list-timers --all --no-legend | grep -c dgm)" -ne 0 ]; then
  echo "STILL PRESENT: a dgm timer remains in list-timers" >&2
  rc=1
fi

# Fold the removal into the /home/lumen/fleet-units baseline, otherwise
# unit-drift check flags the disappearance as unexpected drift next run
# (wq#1656 convention).
sudo -u lumen /home/lumen/bin/unit-drift snapshot
sudo -u lumen /home/lumen/bin/unit-drift check --digest

# Final confirmation is time-based and cannot run here: after the next 17:00 UTC
# boundary, /home/lumen/autonomy/agent5/.dgm_history.jsonl must gain no new line
# (93 lines as of the 2026-09-12T17:14:34Z cycle).
echo "line count now: $(grep -c '' /home/lumen/autonomy/agent5/.dgm_history.jsonl) (expect unchanged after next 17:00 UTC)"

exit "$rc"
