#!/usr/bin/env bash
# wq#3913 lumen-backup-prune mount-wait install. Run as root:
#   /home/lumen/bin/lumen-run start --root --name wq3913-install -- \
#     bash /home/lumen/foreman/ops/wq3913-a1-backup-prune-mount-wait/install.sh
# (this repo's convention for privileged unit installs -- see ops/wq2092-cert-watch).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DROPIN_DIR=/etc/systemd/system/lumen-backup-prune.service.d

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/10-wait-for-mount.conf" "$DROPIN_DIR/10-wait-for-mount.conf"
  vdone
fi

mkdir -p "$DROPIN_DIR"
install -o root -g root -m 644 "$SRC/10-wait-for-mount.conf" \
    "$DROPIN_DIR/10-wait-for-mount.conf"
systemctl daemon-reload

echo "installed lumen-backup-prune.service.d/10-wait-for-mount.conf (RequiresMountsFor=/mnt/lumen-backup)"
echo "verify: systemctl cat lumen-backup-prune.service | grep RequiresMountsFor"
echo "rollback: rm /etc/systemd/system/lumen-backup-prune.service.d/10-wait-for-mount.conf && systemctl daemon-reload"
