#!/bin/bash
# wq#5122 Phase 4: stage Ptah root on Lumen. Ptah is READ-ONLY (rsync sender only).
# Lumen side runs unprivileged with --fake-super (ownership/modes/devices/xattrs kept in
# user.rsync.* xattrs on XFS /scratch); restored to real metadata when pushed to Neith as root.
# Usage: stage-pass.sh <pass-number>
set -u
PASS="${1:?pass number}"
LOG=/scratch/clone/stage-pass${PASS}.log
{
echo "=== pass ${PASS} start $(date -Is)"
rsync -aHAXSx --numeric-ids --delete --fake-super --info=progress2,stats2 \
  --exclude='/dev/***' --exclude='/proc/***' --exclude='/sys/***' \
  --exclude='/run/***' --exclude='/tmp/***' --exclude='/mnt/***' \
  --exclude='/media/***' --exclude='/data/***' --exclude='/lost+found' \
  -e 'ssh -o Compression=no -o BatchMode=yes -o ConnectTimeout=10' \
  --rsync-path='sudo -n rsync' \
  lumen@192.168.8.125:/ /scratch/clone/ptah-root/
RC=$?
echo "=== pass ${PASS} end $(date -Is) rc=${RC}"
} 2>&1 | tr '\r' '\n' | awk 'NR%200==1 || /===|Number|Total|sent|rsync|error|rc=/' > "$LOG"
