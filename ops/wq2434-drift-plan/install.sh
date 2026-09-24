#!/usr/bin/env bash
# Install the drift-plan generator (wq#2434, proposal 9f2866bcda22):
#   drift_plan.py -> /home/lumen/minerva/drift_plan.py
#   plans         -> /home/lumen/minerva/drift_plans/
#   route         -> /api/v1/drift-plans on the live lumen-dashboard aggregator
#   timer         -> lumen-drift-plan.timer, weekly
# Idempotent. Nothing here alerts, gates or routes traffic: plans are artifacts.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/drift_plan.py" /home/lumen/minerva/drift_plan.py
  vunit_file "$HERE/lumen-drift-plan.service" /etc/systemd/system/lumen-drift-plan.service
  vunit_file "$HERE/lumen-drift-plan.timer" /etc/systemd/system/lumen-drift-plan.timer
  venabled lumen-drift-plan.timer
  vactive lumen-drift-plan.timer
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
sudo systemctl enable --now lumen-drift-plan.timer
sudo systemctl restart lumen-dashboard

# ---- health ----------------------------------------------------------------
systemctl is-active --quiet lumen-drift-plan.timer
systemctl is-active --quiet lumen-dashboard
python3 /home/lumen/minerva/drift_plan.py
python3 -c "import json;d=json.load(open('/home/lumen/minerva/drift_plans/index.json'));assert d['counts']['total']>0,d;print('plans:',d['counts'])"
curl -fsS --max-time 10 http://127.0.0.1:8123/api/v1/drift-plans \
  | python3 -c "import json,sys;d=json.load(sys.stdin);assert d.get('available'),d;print('dashboard route ok:',d['counts'])"
systemctl list-timers lumen-drift-plan.timer --no-pager --no-legend
