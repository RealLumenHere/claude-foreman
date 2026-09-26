#!/usr/bin/env bash
# wq#2472: install the wq#2051-fixed journald -> Bes VictoriaLogs forwarder on a fleet node.
# Run as root on the target from a copy of this dir. Idempotent. Refuses on Lumen/Bes,
# which already run their own forwarder units.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
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
