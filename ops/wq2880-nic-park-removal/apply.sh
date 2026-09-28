#!/usr/bin/env bash
# wq#2880: finish the 08-07 nic-park reversal on Lumen. Run AS ROOT, ON LUMEN.
# No reboot, no power cycle. Disables and removes nic-park (unit + script),
# then binds the onboard I217-LM (0000:00:19.0) back to e1000e. Only that one
# device is touched; `nic-park --undo` is deliberately not used because it
# rebinds every unbound NIC-class PCI device by brute force (wq#2757).
# ens1 is never touched. Ends by running verify.py; non-zero exit = not done.
# Idempotent: safe to re-run.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PCI=0000:00:19.0
UNIT=/etc/systemd/system/nic-park.service
SCRIPT=/usr/local/sbin/nic-park
BACKUP=/home/lumen/maint/wq2880-nic-park-backup

[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
# Lumen only: 00:19.0 must be the I217-LM (8086:153a) and ens1 must exist.
[ "$(cat /sys/bus/pci/devices/$PCI/vendor 2>/dev/null)" = 0x8086 ] \
  && [ "$(cat /sys/bus/pci/devices/$PCI/device 2>/dev/null)" = 0x153a ] \
  && [ -e /sys/class/net/ens1 ] \
  || { echo "REFUSING: not Lumen (no I217-LM at $PCI or no ens1)"; exit 1; }

# Keep a copy so the removal is reversible without the maint tooling.
install -d -o lumen -g lumen -m 0755 "$BACKUP"
[ -e "$UNIT" ] && cp -p "$UNIT" "$BACKUP/"
[ -e "$SCRIPT" ] && cp -p "$SCRIPT" "$BACKUP/"

if systemctl cat nic-park.service >/dev/null 2>&1; then
  systemctl disable nic-park.service
fi
rm -f "$UNIT" "$SCRIPT"
systemctl daemon-reload
# The unit is RemainAfterExit=yes, so it still shows active/exited until reset.
systemctl reset-failed nic-park.service 2>/dev/null || true

if [ ! -e /sys/bus/pci/devices/$PCI/driver ]; then
  echo "$PCI" > /sys/bus/pci/drivers/e1000e/bind
fi
for _ in $(seq 1 10); do
  [ -e /sys/class/net/enp0s25 ] && break
  sleep 1
done

python3 "$HERE/verify.py"
