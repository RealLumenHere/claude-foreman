#!/usr/bin/env bash
# wq#1762 — remove the retired lumen-rerank-thread-watch timer/service.
#
# Requires root (the unit files are root:root 0644 under /etc/systemd/system)
# and a caller not bound by the build-lane deny-list on systemctl
# stop/disable/mask (foreman/harness/guard.sh:92-93). Run as:
#
#   sudo bash ops/wq1656-retire-lumen-rerank-thread-watch/remove-units.sh
#
# Retirement rationale and sweep evidence: RUNBOOK.md (this directory),
# docs/fleet/retired_services.yaml (name: lumen-rerank-thread-watch).
# Rollback: RUNBOOK.md "Rollback" — reinstall both unit files and delete
# /home/lumen/minerva/v4/reports/596_watch_ROLLED_BACK to re-arm the watch.
set -euo pipefail

UNITS=(lumen-rerank-thread-watch.timer lumen-rerank-thread-watch.service)

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root (writing /etc/systemd/system)" >&2
  exit 1
fi

echo "== before =="
for u in "${UNITS[@]}"; do
  systemctl show "$u" -p LoadState -p ActiveState -p UnitFileState | tr '\n' ' '
  echo "<- $u"
done

systemctl disable --now lumen-rerank-thread-watch.timer
rm -f /etc/systemd/system/lumen-rerank-thread-watch.timer \
      /etc/systemd/system/lumen-rerank-thread-watch.service
systemctl daemon-reload
systemctl reset-failed lumen-rerank-thread-watch.timer lumen-rerank-thread-watch.service 2>/dev/null || true

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

# Fold the removal into the /home/lumen/fleet-units baseline, otherwise
# unit-drift check flags the disappearance as unexpected drift next run.
sudo -u lumen /home/lumen/bin/unit-drift snapshot
sudo -u lumen /home/lumen/bin/unit-drift check --digest

exit "$rc"
