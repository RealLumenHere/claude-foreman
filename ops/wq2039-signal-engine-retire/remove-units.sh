#!/usr/bin/env bash
# wq#2039 — remove the retired lumen-signal-engine timer/service.
#
# Requires root (the unit files are root:root under /etc/systemd/system) and a
# caller not bound by the build-lane deny-list on systemctl stop/disable/mask
# (foreman/harness/guard.sh:92-93). Run as:
#
#   sudo bash ops/wq2039-signal-engine-retire/remove-units.sh
#
# Retirement rationale and source-probe evidence: RUNBOOK.md (this directory),
# docs/fleet/retired_services.yaml (name: signal-engine).
# Rollback: RUNBOOK.md "Rollback" — unit files are preserved verbatim in
# units/ in this directory (not units.bak/ — .gitignore eats *.bak).
set -euo pipefail

UNITS=(lumen-signal-engine.timer lumen-signal-engine.service)
DROPIN_DIR=/etc/systemd/system/lumen-signal-engine.service.d
STAMP=/home/lumen/logs/.stamp.lumen-signal-engine
DEADMAN_STATE=/home/lumen/foreman/ops/wq1184-gatus-health-checks/deadman_state.json

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root (writing /etc/systemd/system)" >&2
  exit 1
fi

echo "== before =="
for u in "${UNITS[@]}"; do
  systemctl show "$u" -p LoadState -p ActiveState -p UnitFileState | tr '\n' ' '
  echo "<- $u"
done

systemctl disable --now lumen-signal-engine.timer
rm -f /etc/systemd/system/lumen-signal-engine.timer \
      /etc/systemd/system/lumen-signal-engine.service
rm -rf "$DROPIN_DIR"
systemctl daemon-reload
systemctl reset-failed lumen-signal-engine.timer lumen-signal-engine.service 2>/dev/null || true

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

# Dead-man leftovers (wq#1184). With the timer gone, cron_deadman.py derives no
# cadence for this unit and skips the stamp entirely (cron_deadman.py:110-115),
# so neither of these can page — they are tidy-up, not alert suppression. Both
# steps are non-fatal: a failure here must not leave the unit half-removed.
rm -f "$STAMP" || true
python3 - "$DEADMAN_STATE" <<'PY' || echo "deadman_state prune skipped (non-fatal)" >&2
import json, os, sys
path = sys.argv[1]
with open(path) as f:
    state = json.load(f)
if state.pop("lumen-signal-engine", None) is not None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    print("deadman_state: pruned lumen-signal-engine")
else:
    print("deadman_state: no lumen-signal-engine row (already pruned)")
PY

# Register both units as deliberately removed (wq#1041) so a reappearance at
# top-level reads as a respawn rather than as a fresh install. Idempotent.
# This line is also the follow-up ticket's liveness probe:
#   file_contains(/home/lumen/fleet-units/known-removed.txt, lumen-signal-engine.timer)
KNOWN_RM=/home/lumen/fleet-units/known-removed.txt
if [ "$rc" -eq 0 ]; then
  for u in "${UNITS[@]}"; do
    grep -qxF "$u" "$KNOWN_RM" || echo "$u" | sudo -u lumen tee -a "$KNOWN_RM" >/dev/null
  done
fi

# Fold the removal into the /home/lumen/fleet-units baseline, otherwise
# unit-drift check flags the disappearance as unexpected drift next run.
sudo -u lumen /home/lumen/bin/unit-drift snapshot
sudo -u lumen /home/lumen/bin/unit-drift check --digest

exit "$rc"
