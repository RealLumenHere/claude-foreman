#!/usr/bin/env bash
# wq#2910 backup-important log hygiene + failure alerting install. Run as root:
#   lumen-run --root -- bash /home/lumen/foreman/ops/wq2910-a1-backup-important-hardening/install.sh
# (this repo's convention for privileged unit installs -- see ops/wq2092-cert-watch).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DROPIN_DIR=/etc/systemd/system/lumen-backup-important.service.d

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
# Added by wq#2943 after wq#2932 found this bundle sat ABSENT 0/2 for a day with
# nothing able to page on it: no handler means ops-deploy-drift only reports it
# heuristically and never opens an aq row.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/backup-important-logs.logrotate" /etc/logrotate.d/lumen-backup-important
  vunit_file "$SRC/90-onfailure-alert.conf" "$DROPIN_DIR/90-onfailure-alert.conf"
  vdone
fi

install -o root -g root -m 644 "$SRC/backup-important-logs.logrotate" /etc/logrotate.d/lumen-backup-important

mkdir -p "$DROPIN_DIR"
install -o root -g root -m 644 "$SRC/90-onfailure-alert.conf" \
    "$DROPIN_DIR/90-onfailure-alert.conf"
systemctl daemon-reload

echo "installed /etc/logrotate.d/lumen-backup-important (size 50M, rotate 4, copytruncate)"
echo "installed lumen-backup-important.service.d/90-onfailure-alert.conf (OnFailure=ntfy-onfailure@%n.service)"
echo "verify logrotate:  logrotate -d /etc/logrotate.d/lumen-backup-important"
echo "verify OnFailure:  systemctl cat lumen-backup-important.service | grep OnFailure"
echo "rollback:          rm /etc/logrotate.d/lumen-backup-important /etc/systemd/system/lumen-backup-important.service.d/90-onfailure-alert.conf && systemctl daemon-reload"
