#!/bin/bash
# wq#5122 Phase 5 (maskrom route): push staged Ptah tree into loop-mounted Neith p1 image on Maksoor.
# --fake-super applies to Lumen (sender) only -> reads user.rsync.* xattrs; Maksoor receiver runs as root.
LOG=/scratch/clone/push-maksoor.log
{
echo "=== push start $(date -Is)"
rsync -aHAXS --numeric-ids --fake-super --delete --info=progress2,stats2 \
  -e 'ssh -o Compression=no -o BatchMode=yes' --rsync-path='sudo -n rsync' \
  /scratch/clone/ptah-root/ maksoor:/mnt/neith-p1/
echo "=== push end $(date -Is) rc=$?"
} 2>&1 | tr '\r' '\n' | awk 'NR%300==1 || /===|Number|Total|sent|rsync|error|rc=/' > "$LOG"
