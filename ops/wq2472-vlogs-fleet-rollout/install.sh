#!/usr/bin/env bash
# wq#2472: install the wq#2051-fixed journald -> Bes VictoriaLogs forwarder on a fleet node.
# Run as root on the target from a copy of this dir. Idempotent. Refuses on Lumen/Bes,
# which already run their own forwarder units.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step
# AND before the root check, so a liveness timer can run it as the lumen user.
# This is a per-node bundle that refuses on Lumen and Bes, so a Lumen-local
# check would report it absent however complete the rollout is -- that is
# exactly what wq#1732 recorded. It asks the nodes it was rolled out to
# (wq#2472 done notes). An unreachable node is ERR, never MISS.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  for h in anubis hathor isis maat sobek thoth khepri; do
    vremote_up "$h" || continue
    vremote_file "$h" "$HERE/forward-journal.py"     /opt/vlogs-forwarder/forward-journal.py
    vremote_file "$h" "$HERE/vlogs-forwarder.service" \
                      /etc/systemd/system/vlogs-forwarder.service
    vremote_file "$h" "$HERE/vlogs-forwarder.timer" \
                      /etc/systemd/system/vlogs-forwarder.timer
    vremote_unit "$h" vlogs-forwarder.timer
  done
  vdone
fi

[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
command -v python3 >/dev/null || { echo "REFUSING: no python3"; exit 1; }
getent group systemd-journal >/dev/null || { echo "REFUSING: no systemd-journal group"; exit 1; }
case "$(hostname -s)" in lumen|bes) echo "REFUSING: $(hostname -s) already forwards"; exit 1;; esac
install -D -m 0755 -o root -g root "$HERE/forward-journal.py" /opt/vlogs-forwarder/forward-journal.py
install -D -m 0644 "$HERE/vlogs-forwarder.service" /etc/systemd/system/vlogs-forwarder.service
install -D -m 0644 "$HERE/vlogs-forwarder.timer" /etc/systemd/system/vlogs-forwarder.timer
systemctl daemon-reload
systemctl enable --now vlogs-forwarder.timer
systemctl start vlogs-forwarder.service
sleep 2
systemctl show vlogs-forwarder.service -p Result,ExecMainStatus
echo "installed on $(hostname -s)"
