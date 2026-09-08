#!/usr/bin/env bash
# wq#1501 — install the Minerva verify-judge drop-in (free/local rung, no phantom
# fallback, 230s timeout, n=1). Run as root on the Minerva host, detached:
#   /home/lumen/bin/lumen-run start --root --name wq1501-judge -- \
#       /home/lumen/foreman/ops/wq1501-minerva-judge-rung/apply.sh
# Idempotent: identical file -> no copy, no daemon-reload. Never restarts the
# service; the env takes effect on the next timer start.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/40-judge-model.conf"
DEST_DIR=/etc/systemd/system/minerva-proposal.service.d
DEST="$DEST_DIR/40-judge-model.conf"
BACKUP_DIR=/home/lumen/backups/wq1500-1501

if [ -f "$DEST" ] && cmp -s "$SRC" "$DEST"; then
    echo "unchanged: $DEST"
else
    mkdir -p "$DEST_DIR" "$BACKUP_DIR"
    if [ -f "$DEST" ]; then
        bak="$BACKUP_DIR/$(date -u +%Y%m%dT%H%M%SZ).40-judge-model.conf.bak"
        cp -p "$DEST" "$bak"
        echo "backup: $bak"
    fi
    install -m 0644 "$SRC" "$DEST"
    systemctl daemon-reload
    echo "installed: $DEST (daemon-reload done)"
fi

# Effective env after the drop-in chain (the last VERIFY_JUDGE_TIMEOUT wins).
systemctl show -p Environment minerva-proposal.service \
    | grep -o -e 'VERIFY_JUDGE[A-Z_0-9]*=[^ ]*' -e 'JUDGE_CTX_CHARS=[^ ]*' || true
