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

# The one non-lumen reader under _runs is uid tokeneng, which re-reads the 0444 grants
# (grant.json, gateway-grant.json, ticket-end-grant-aN.json) by path. So: run dirs 0711
# (traverse, never list), grants untouched, everything else owner-only (wq#2911).
RUNS=/home/lumen/foreman/_runs
GRANT="$RUNS/wq[0-9]+/[^/]*grant[^/]*\.json"   # run-dir root only; snapshots are not grants
find "$RUNS" -regextype posix-extended -type f -perm -o+r ! -regex "$GRANT" \
  -exec chmod go-rwx {} +
find "$RUNS" -mindepth 2 -type d -perm -o+r -exec chmod 0700 {} +
find "$RUNS" -mindepth 1 -maxdepth 1 -type d -name 'wq*' ! -perm 0711 -exec chmod 0711 {} +
echo "_runs: world-readable non-grant files left:" \
  "$(find "$RUNS" -regextype posix-extended -type f -perm -o+r ! -regex "$GRANT" | grep -c ^ || true)"
