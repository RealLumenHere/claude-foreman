#!/usr/bin/env bash
# Install the wq#3215 stale-env-audit extension (--docker, --raise-aq, --fail-on-drift)
# and the hourly aq timer. Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile      "$HERE/stale-env-audit"                    /home/lumen/bin/stale-env-audit
  vunit_file "$HERE/lumen-stale-env-audit.service"       /etc/systemd/system/lumen-stale-env-audit.service
  vunit_file "$HERE/lumen-stale-env-audit.timer"         /etc/systemd/system/lumen-stale-env-audit.timer
  venabled   lumen-stale-env-audit.timer
  vactive    lumen-stale-env-audit.timer
  vdone
fi

python3 -m py_compile "$HERE/stale-env-audit"
python3 -m pytest -q "$HERE/test_stale_env_audit.py"

install -m 0755 "$HERE/stale-env-audit" /home/lumen/bin/stale-env-audit
sudo install -m 0644 "$HERE/lumen-stale-env-audit.service" /etc/systemd/system/lumen-stale-env-audit.service
sudo install -m 0644 "$HERE/lumen-stale-env-audit.timer" /etc/systemd/system/lumen-stale-env-audit.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-stale-env-audit.timer
systemctl is-active --quiet lumen-stale-env-audit.timer
systemctl list-timers lumen-stale-env-audit.timer --no-pager --no-legend

# One live report (no --raise-aq: the timer owns aq state; this run persists nothing).
sudo /home/lumen/bin/stale-env-audit --docker
