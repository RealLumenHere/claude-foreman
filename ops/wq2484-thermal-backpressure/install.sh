#!/usr/bin/env bash
# Install wq#2484's thermal-backpressure timer. Idempotent. Run from a root/interactive
# session on Lumen: the BUILD worker that wrote this has no sudo and cannot install a unit.
#
# wq#4714 rebased what this installs before it was ever installed: the roster is the live
# fleet, the sensor is hwmon sysfs rather than netdata, and the poll is bounded so one dead
# node cannot hang it. Reasoning: design/thermal_backpressure_rebase.md.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
GUARD=/home/lumen/foreman/ops/thermal_backpressure.py
UNITS="lumen-thermal-backpressure.service lumen-thermal-backpressure.timer"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/lumen-thermal-backpressure.service" \
             /etc/systemd/system/lumen-thermal-backpressure.service
  vunit_file "$SRC/lumen-thermal-backpressure.timer" \
             /etc/systemd/system/lumen-thermal-backpressure.timer
  venabled   lumen-thermal-backpressure.timer
  vactive    lumen-thermal-backpressure.timer
  # wq#4733: vactive alone cannot tell SubState=waiting (will fire again) from
  # SubState=elapsed (will not, until a reboot) — see wq#4732's judgment report.
  vtimer_scheduled lumen-thermal-backpressure.timer
  # The payload is not installed by this bundle — it is read in place from the git-tracked
  # foreman tree — so assert the version of it the units actually run. A guard without
  # sample_temps is one still reading netdata serially, which is what cost wq#3044 its
  # install (rc=124); and a config without poll_budget_s loads fine and hangs in the field.
  # vcontains, not vflag: the guard is 0644 and has no --help entry for a subparser arg.
  vcontains "$GUARD" '^def sample_temps\('
  vcontains "$GUARD" 'poll_budget_s'
  vcontains /home/lumen/foreman/ops/thermal_backpressure.yaml '^  poll_budget_s:'
  vdone
fi

# read-only first: every node should answer with a temperature (now=) and held=False
python3 "$GUARD" sample
echo
python3 "$GUARD" poll --dry-run
echo

sudo install -m 0644 "$SRC/lumen-thermal-backpressure.service" /etc/systemd/system/lumen-thermal-backpressure.service
sudo install -m 0644 "$SRC/lumen-thermal-backpressure.timer" /etc/systemd/system/lumen-thermal-backpressure.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-thermal-backpressure.timer
# wq#4733: a fresh enable on a long-running box leaves OnUnitActiveSec with no base (the
# service has never left "inactive" this boot) until something runs it once. Start it here
# so the first run happens under the unit (not just this script's direct `python3` calls
# above) and anchors the recurring timer instead of waiting on OnActiveSec=0 to race it.
sudo systemctl start lumen-thermal-backpressure.service
systemctl list-timers lumen-thermal-backpressure.timer --no-pager --no-legend
echo
python3 "$GUARD" status
echo
# declared units, blessed last so this deploy does not open a unit-drift alert (wq#3579)
/home/lumen/bin/unit-drift accept --ticket wq#4714 $UNITS
