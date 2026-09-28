#!/usr/bin/env bash
# wq#2911: pat-scan CRITICAL = live token only (world-readable dead literal -> WARN), plus
# the one-off tightening of evidence already written world-readable under _runs.
# Unprivileged (runs as lumen): /home/lumen/bin and _runs are both lumen-owned.
# Idempotent: refuses if the live pat-scan is neither the known pre-image nor already ours.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/bin/wq523_pat_residue.py
PRE=37fee4bc569b80cca9e28db9d810c6f66bdc69541203a192806bfed26ec13dcd
NEW=$(sha256sum "$SRC/staged/wq523_pat_residue.py" | cut -d' ' -f1)
CUR=$(sha256sum "$LIVE" | cut -d' ' -f1)

if [ "$CUR" = "$NEW" ]; then
  echo "pat-scan: already installed"
elif [ "$CUR" = "$PRE" ]; then
  /home/lumen/bin/install-script "$SRC/staged/wq523_pat_residue.py" "$LIVE"   # rename, wq#2856
  echo "pat-scan: installed ($NEW)"
else
  echo "pat-scan: live file changed underneath ($CUR) — refusing to overwrite" >&2
  exit 1
fi

# Nothing under _runs is served to another user (no ACL, no non-lumen reader; wq#2911).
RUNS=/home/lumen/foreman/_runs
find "$RUNS" -type f -perm -o+r -exec chmod o-r,g-r {} +
find "$RUNS" -mindepth 1 -maxdepth 1 -type d -name 'wq*' -perm -o+r -exec chmod 0700 {} +
echo "_runs: world-readable files left: $(find "$RUNS" -type f -perm -o+r | grep -c ^ || true)"
