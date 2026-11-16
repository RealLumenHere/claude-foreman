#!/usr/bin/env bash
# wq#4956 / wq#5009 auto-dispatch bundle: the dispatcher script (v3, park-and-resume hook),
# its units and its tests, tracked here. Before this bundle the script existed only as an
# untracked file in /home/lumen/bin — no history, no review, no rollback.
#
#   bash /home/lumen/foreman/ops/wq4956-auto-dispatch/install.sh            install / update
#   bash /home/lumen/foreman/ops/wq4956-auto-dispatch/install.sh --verify   read-only check
#   bash /home/lumen/foreman/ops/wq4956-auto-dispatch/install.sh --dry-run  say what would change
#
# NEVER enables or starts lumen-auto-dispatch.timer. Enabling is Frank's call:
#   sudo systemctl enable --now lumen-auto-dispatch.timer
# (read the ENABLEMENT NOTES at the top of /home/lumen/foreman/auto-dispatch.yaml first).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/home/lumen/bin/auto-dispatch
UNITS=/etc/systemd/system

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/auto-dispatch" "$BIN"
  vunit_file "$HERE/lumen-auto-dispatch.service" "$UNITS/lumen-auto-dispatch.service"
  vunit_file "$HERE/lumen-auto-dispatch.timer"   "$UNITS/lumen-auto-dispatch.timer"
  vflag "$BIN" --dry-run
  vcontains "$BIN" "park_sweep"
  vcontains /home/lumen/foreman/harness/park.py "def sweep("
  vdone
fi

DRY=0
[ "${1:-}" = --dry-run ] && DRY=1

if cmp -s "$HERE/auto-dispatch" "$BIN"; then
  echo "unchanged: $BIN"
else
  if [ "$DRY" = 1 ]; then
    echo "would install: $HERE/auto-dispatch -> $BIN"
  else
    # cp-to-tmp then mv -f: a pass that is running keeps its old inode (wq#2856).
    tmp="$BIN.tmp.$$"
    cp "$HERE/auto-dispatch" "$tmp"
    chmod 0755 "$tmp"
    mv -f "$tmp" "$BIN"
    echo "installed: $BIN"
  fi
fi

for u in lumen-auto-dispatch.service lumen-auto-dispatch.timer; do
  if cmp -s "$HERE/$u" "$UNITS/$u"; then
    echo "unchanged: $UNITS/$u"
  elif [ "$DRY" = 1 ]; then
    echo "would install: $HERE/$u -> $UNITS/$u (sudo)"
  else
    sudo install -m 0644 "$HERE/$u" "$UNITS/$u"
    sudo systemctl daemon-reload
    echo "installed: $UNITS/$u"
  fi
done

echo "--- state ---"
echo "timer enabled: $(systemctl is-enabled lumen-auto-dispatch.timer 2>/dev/null || true) (this script never changes that)"
echo "tests:    python3 $HERE/test_auto_dispatch.py"
echo "dry run:  $BIN --dry-run"
echo "rollback: cp /home/lumen/foreman/auto-dispatch/auto-dispatch.v2.bak.20261116 $BIN  (pre-park v2)"
