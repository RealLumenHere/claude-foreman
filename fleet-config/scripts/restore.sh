#!/usr/bin/env bash
# Restore from a scripts/backup.sh snapshot. Interactive-free, but each
# stage is opt-in via flags so a partial recovery never clobbers live state.
# Usage: ./restore.sh <snapshot-dir> [--wq] [--configs] [--crontab] [--all]
# Walkthrough with verification steps: docs/RECOVERY.md §1.
set -euo pipefail
SNAP=${1:?usage: restore.sh <snapshot-dir> [--wq] [--configs] [--crontab] [--all]}
shift
[ -d "$SNAP" ] || { echo "no such snapshot: $SNAP"; exit 1; }

DO_WQ=0; DO_CFG=0; DO_CRON=0
while [ $# -gt 0 ]; do
  case "$1" in
    --wq) DO_WQ=1 ;;
    --configs) DO_CFG=1 ;;
    --crontab) DO_CRON=1 ;;
    --all) DO_WQ=1; DO_CFG=1; DO_CRON=1 ;;
    *) echo "unknown flag $1"; exit 2 ;;
  esac
  shift
done

if [ "$DO_WQ" = 1 ]; then
  mkdir -p /home/lumen/agents/work-queue
  if [ -f /home/lumen/agents/work-queue/queue.db ]; then
    cp /home/lumen/agents/work-queue/queue.db \
       "/home/lumen/agents/work-queue/queue.db.pre-restore.$(date -u +%s)"
  fi
  cp "$SNAP/queue.db" /home/lumen/agents/work-queue/queue.db
  tar -xzf "$SNAP/work-queue-tooling.tgz" -C /home/lumen/agents
  sqlite3 /home/lumen/agents/work-queue/queue.db "PRAGMA integrity_check;"
  echo "wq restored — verify: /home/lumen/bin/wq list"
fi

if [ "$DO_CFG" = 1 ]; then
  # extracts under / : litellm config, fleet-compose, systemd units, secrets
  sudo tar -xzf "$SNAP/configs.tgz" -C /
  sudo systemctl daemon-reload
  echo "configs restored — re-enable units per docs/RECOVERY.md §2"
fi

if [ "$DO_CRON" = 1 ] && [ -s "$SNAP/crontab.txt" ]; then
  crontab "$SNAP/crontab.txt"
  echo "crontab restored"
fi
