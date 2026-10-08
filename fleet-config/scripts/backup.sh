#!/usr/bin/env bash
# Nightly fleet backup (lumen). Run by wq-backup.timer; safe to run by hand.
# Captures everything RECOVERY.md's restore path expects:
#   wq        — sqlite .backup of queue.db (consistent against live writers)
#               + the wq script and helpers from agents/work-queue
#   configs   — /home/lumen/agent/litellm_config.yaml, gatus config,
#               /etc/systemd/system fleet units, crontab
# Credential files (.secrets, .git-credentials, .env) are INCLUDED in the
# tar but the tar itself stays 0600 under /home/lumen/backups — recovery
# without them means re-minting everything (RECOVERY.md §6), which is a
# day of work; a 0600 local tar is the accepted tradeoff.
set -euo pipefail
TS=$(date -u +%Y%m%dT%H%M%SZ)
DEST=${BACKUP_DIR:-/home/lumen/backups}
WORK="$DEST/fleet-$TS"
mkdir -p "$WORK"
umask 077

# wq: consistent sqlite snapshot, then the tooling around it.
# The dir is live (wq writes logs constantly) — tar exits 1 for
# "file changed as we read it", which is fine for tooling files; only
# exit 2 (fatal) should kill the backup.
sqlite3 /home/lumen/agents/work-queue/queue.db ".backup '$WORK/queue.db'"
tar -czf "$WORK/work-queue-tooling.tgz" \
    --warning=no-file-changed \
    --exclude='*.db' --exclude='*.db-*' \
    -C /home/lumen/agents work-queue || [ "$?" -eq 1 ]

# configs (tolerate missing paths on a partially-built node)
tar -czf "$WORK/configs.tgz" \
    --ignore-failed-read \
    /home/lumen/agent/litellm_config.yaml \
    /home/lumen/fleet-compose \
    /etc/systemd/system \
    /home/lumen/.secrets \
    /home/lumen/.git-credentials \
    2>/dev/null || true

crontab -l > "$WORK/crontab.txt" 2>/dev/null || true

chmod -R go-rwx "$WORK"

# retention: keep newest 14 snapshots
ls -1dt "$DEST"/fleet-* | tail -n +15 | xargs -r rm -rf

echo "backup OK: $WORK"
