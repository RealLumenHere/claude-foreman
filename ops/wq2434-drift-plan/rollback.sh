#!/usr/bin/env bash
# Rollback for wq#2434. Matches the proposal's declared rollback: delete the
# generator and the plans; the dashboard panel is read-only and degrades to empty.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

sudo systemctl disable --now lumen-drift-plan.timer || true
sudo rm -f /etc/systemd/system/lumen-drift-plan.service /etc/systemd/system/lumen-drift-plan.timer
sudo systemctl daemon-reload

python3 "$HERE/patch_dashboard.py" --revert
sudo systemctl restart lumen-dashboard

rm -f /home/lumen/minerva/drift_plan.py
rm -rf /home/lumen/minerva/drift_plans
echo "wq#2434 rolled back (drift_plan_overrides.json, if any, is left alone — it is human-authored)"
