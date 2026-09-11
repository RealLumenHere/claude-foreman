#!/usr/bin/env bash
# wq#1766: install the wq#1664 brain-escalate drop-in on the live host before
# PR #116 (src/live_agent.ts config gate) merges — otherwise the next restart
# of lumen-pokemon-live exits 2 because 30-brain-env.conf currently pins
# LPP_BRAIN_ESCALATE and LPP_BRAIN_ACTRETRY equal to LPP_BRAIN_PRIMARY.
#
# Root-only (writes under /etc/systemd/system). Run via:
#   /home/lumen/bin/lumen-run start --root -- \
#     bash /home/lumen/worktrees/wq1766-a1/ops/wq1766-lpp-brain-escalate-dropin/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/35-brain-escalate-1664.conf"
DEST_DIR=/etc/systemd/system/lumen-pokemon-live.service.d
DEST="$DEST_DIR/35-brain-escalate-1664.conf"

install -m 644 "$SRC" "$DEST"
systemctl daemon-reload
systemctl restart lumen-pokemon-live
sleep 3

systemctl is-active --quiet lumen-pokemon-live
if journalctl -u lumen-pokemon-live --no-pager -n 40 | grep -qa '\[config\] FATAL'; then
  echo "wq1766: FATAL found in journal after restart — install did not fix the boot gate" >&2
  exit 1
fi

echo "wq1766: 35-brain-escalate-1664.conf installed, lumen-pokemon-live active, no [config] FATAL"
