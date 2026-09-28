#!/usr/bin/env bash
# wq#2910 backup-important log hygiene + failure alerting install. Run as root:
#   lumen-run --root -- bash /home/lumen/foreman/ops/wq2910-a1-backup-important-hardening/install.sh
# (this repo's convention for privileged unit installs -- see ops/wq2092-cert-watch).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

install -o root -g root -m 644 "$SRC/backup-important-logs.logrotate" /etc/logrotate.d/lumen-backup-important

mkdir -p /etc/systemd/system/lumen-backup-important.service.d
install -o root -g root -m 644 "$SRC/90-onfailure-alert.conf" \
    /etc/systemd/system/lumen-backup-important.service.d/90-onfailure-alert.conf
systemctl daemon-reload

echo "installed /etc/logrotate.d/lumen-backup-important (size 50M, rotate 4, copytruncate)"
echo "installed lumen-backup-important.service.d/90-onfailure-alert.conf (OnFailure=ntfy-onfailure@%n.service)"
echo "verify logrotate:  logrotate -d /etc/logrotate.d/lumen-backup-important"
echo "verify OnFailure:  systemctl cat lumen-backup-important.service | grep OnFailure"
echo "rollback:          rm /etc/logrotate.d/lumen-backup-important /etc/systemd/system/lumen-backup-important.service.d/90-onfailure-alert.conf && systemctl daemon-reload"
