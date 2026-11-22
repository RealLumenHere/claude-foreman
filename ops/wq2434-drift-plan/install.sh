#!/usr/bin/env bash
# Install the drift-plan generator (wq#2434, proposal 9f2866bcda22):
#   drift_plan.py -> /home/lumen/minerva/drift_plan.py
#   plans         -> /home/lumen/minerva/drift_plans/
#   route         -> /api/v1/drift-plans on the live lumen-dashboard aggregator
#   timer         -> lumen-drift-plan.timer, weekly
# Idempotent. Nothing here alerts, gates or routes traffic: plans are artifacts.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# wq#5263: 0 = intended-off (disabled by the 2026-10-08 subtraction,
# scratch/subtraction/timers-disable-list.txt:56). A deliberate re-enable flips this to 1,
# which makes install enable+start the timer and --verify assert it enabled, active and
# scheduled. Not an env override: the intent lives here.
DRIFT_PLAN_ON=0

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/drift_plan.py" /home/lumen/minerva/drift_plan.py
  vunit_file "$HERE/lumen-drift-plan.service" /etc/systemd/system/lumen-drift-plan.service
  vunit_file "$HERE/lumen-drift-plan.timer" /etc/systemd/system/lumen-drift-plan.timer
  # wq#5263: lumen-drift-plan.timer is intended-off (2026-10-08 subtraction). wq#4910
  # dropped vtimer_scheduled for that reason but left venabled/vactive, so --verify failed
  # forever and filed an ops-root-apply telling the reader to run this script as root,
  # whose `enable --now` would have undone the subtraction (same class as wq#5259/wq#5261).
  # The unit files are still verified; the timer's state is not asserted. Re-enabling it is
  # a decision, not a heal: whoever makes it sets DRIFT_PLAN_ON=1 above.
  if [ "$DRIFT_PLAN_ON" = 1 ]; then
    venabled lumen-drift-plan.timer
    vactive lumen-drift-plan.timer
    vtimer_scheduled lumen-drift-plan.timer
  fi
  vcontains /home/lumen/dashboard/dashboard_api.py '# --- wq#2434 drift plans'
  vdone
fi

python3 -m pytest -q "$HERE/test_drift_plan.py"

# Precheck before any write: the live config parses and the live overrides file
# (if a human has written one) validates. A bad override exits 2 here, untouched.
python3 "$HERE/drift_plan.py" --dry-run

install -m 0755 "$HERE/drift_plan.py" /home/lumen/minerva/drift_plan.py
mkdir -p /home/lumen/minerva/drift_plans

# Read-only dashboard surface. Backs up dashboard_api.py and refuses to install a
# file that does not compile, so a failure here leaves the aggregator untouched.
python3 "$HERE/patch_dashboard.py"

sudo install -m 0644 "$HERE/lumen-drift-plan.service" /etc/systemd/system/lumen-drift-plan.service
sudo install -m 0644 "$HERE/lumen-drift-plan.timer" /etc/systemd/system/lumen-drift-plan.timer
sudo systemctl daemon-reload
if [ "$DRIFT_PLAN_ON" = 1 ]; then
  sudo systemctl enable --now lumen-drift-plan.timer
else
  # wq#5263: intended-off; installing files must not undo the 2026-10-08 subtraction.
  echo "lumen-drift-plan units installed; timer left as-is (intended-off, DRIFT_PLAN_ON=0)"
fi
sudo systemctl restart lumen-dashboard

# ---- health ----------------------------------------------------------------
if [ "$DRIFT_PLAN_ON" = 1 ]; then
  systemctl is-active --quiet lumen-drift-plan.timer
fi
systemctl is-active --quiet lumen-dashboard
python3 /home/lumen/minerva/drift_plan.py
python3 -c "import json;d=json.load(open('/home/lumen/minerva/drift_plans/index.json'));assert d['counts']['total']>0,d;print('plans:',d['counts'])"
# `systemctl restart` returns when the unit is started, not when uvicorn is
# listening — curl one millisecond later gets ECONNREFUSED and fails a health
# check on a dashboard that is in fact fine (wq#2434 first install run).
for _ in $(seq 30); do
  curl -fsS --max-time 10 -o /tmp/wq2434-drift-route.json \
    http://127.0.0.1:8123/api/v1/drift-plans && break
  sleep 1
done
python3 -c "import json;d=json.load(open('/tmp/wq2434-drift-route.json'));assert d.get('available'),d;print('dashboard route ok:',d['counts'])"
systemctl list-timers lumen-drift-plan.timer --no-pager --no-legend
