#!/bin/bash
# wq#1704 — install the four SPOF watchdogs FLEET_SERVICE_ATLAS §5.1 left open.
# Run as root via `lumen-run --root` (deploy step, NOT run by the build lane).
# Touches nothing it watches: no restarts, no unit edits outside its own four
# service/timer pairs.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CHECKS=(daemons roundtrip restarts timers)

echo "== gate: the suite must pass before anything is installed =="
sudo -u lumen python3 -m pytest "$SRC/tests" -q

echo "== gate: each watchdog must fire on a simulated failure =="
sudo -u lumen python3 "$SRC/verify_fires.py"

echo "== script -> /home/lumen/bin/fleet-spof-watch =="
install -o lumen -g lumen -m 755 "$SRC/fleet_spof_watch.py" \
  /home/lumen/bin/fleet-spof-watch
install -d -o lumen -g lumen -m 755 /home/lumen/state/spof-watch

echo "== live smoke against real systemd, throwaway alerts.db =="
sudo -u lumen python3 "$SRC/smoke.py"

echo "== units =="
for c in "${CHECKS[@]}"; do
  install -m 644 "$SRC/lumen-spof-$c.service" \
    "/etc/systemd/system/lumen-spof-$c.service"
  install -m 644 "$SRC/lumen-spof-$c.timer" \
    "/etc/systemd/system/lumen-spof-$c.timer"
done
systemctl daemon-reload
for c in "${CHECKS[@]}"; do
  systemctl enable --now "lumen-spof-$c.timer"
done
systemctl list-timers 'lumen-spof-*' --no-pager || true

echo "== first real pass of each check (against the live alerts.db) =="
for c in "${CHECKS[@]}"; do
  systemctl start "lumen-spof-$c.service"
  systemctl show "lumen-spof-$c.service" -p Result --value
done

echo "== round-trip proof: this is the one that needs the live alert path =="
journalctl -u lumen-spof-roundtrip.service -n 5 --no-pager

cat <<'EOF'

Installed. Findings land on the live queue as source=spof-watch:

  aq list --source spof-watch

Findings exit 0 by design (wq#1657), so `systemctl --failed` stays meaningful:
a red lumen-spof-* unit means the guard itself crashed, and
ntfy-onfailure@lumen-spof-<check>.service will have already paged.

Rollback:  systemctl disable --now lumen-spof-{daemons,roundtrip,restarts,timers}.timer
           rm /etc/systemd/system/lumen-spof-*.{service,timer} && systemctl daemon-reload
EOF
