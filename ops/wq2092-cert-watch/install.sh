#!/usr/bin/env bash
# wq#2092 lumen-cert-watch install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2092-cert-watch/install.sh
# (this repo's convention for privileged unit installs — see
# ops/wq1397-gitea-version-check).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/lumen-cert-watch
# wq#5259: 0 = intended-off (disabled by the 2026-10-08 subtraction). A deliberate
# re-enable flips this to 1, which makes install enable+start the timer and --verify
# assert it enabled, active and scheduled. Not an env override: the intent lives here.
CERT_WATCH_ON=0

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install
# step. Added by wq#2092's own close pass: the bundle landed 2026-09-19 with no
# handler, so ops-deploy-drift could only guess at it heuristically and never
# opened an aq row — the same blind spot wq#2932 found on backup-important, and
# the reason lumen-cert-watch.timer sat inactive for nine days unnoticed.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/cert_watch.py" "$DEST/cert_watch.py"
  # targets.json is operator-edited, so match the shape, not the bytes. Parse it
  # with the shipped loader rather than grepping for "extra": a hand-edit typo
  # used to pass this check while every daily run silently lost the extra
  # targets (wq#3008). Read-only: load_extra_targets only opens the file.
  vcontains "$DEST/targets.json" '"extra"'
  terr="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import cert_watch; print(cert_watch.load_extra_targets(sys.argv[2])[1] or "")' \
          "$SRC" "$DEST/targets.json" 2>&1)" || terr="targets.json check failed: $terr"
  [ -z "$terr" ] || _vmiss "$terr"
  vunit_file "$SRC/lumen-cert-watch.service" /etc/systemd/system/lumen-cert-watch.service
  vunit_file "$SRC/lumen-cert-watch.timer" /etc/systemd/system/lumen-cert-watch.timer
  # wq#5259: lumen-cert-watch.timer is intended-off: the 2026-10-08 subtraction disabled it
  # on purpose (/home/lumen/scratch/subtraction/timers-disable-phase2.txt:14). wq#4910
  # dropped vtimer_scheduled for that reason but left vactive/venabled, so --verify failed
  # forever and filed an ops-root-apply telling the reader to run this script as root,
  # whose `enable --now` would have undone the subtraction (same class as wq#5257). The
  # unit files and installed copy are still verified; the timer's state is not asserted.
  # Re-enabling it is a decision, not a heal: whoever makes it sets CERT_WATCH_ON=1 above.
  if [ "$CERT_WATCH_ON" = 1 ]; then
    vactive lumen-cert-watch.timer
    venabled lumen-cert-watch.timer
    vtimer_scheduled lumen-cert-watch.timer
  fi
  vdone
fi

mkdir -p "$DEST"
install -o lumen -g lumen -m 755 "$SRC/cert_watch.py" "$DEST/cert_watch.py"
# targets.json is operator-edited (extra internal endpoints): never overwrite it.
[ -e "$DEST/targets.json" ] || install -o lumen -g lumen -m 644 "$SRC/targets.json" "$DEST/targets.json"

cp "$SRC/lumen-cert-watch.service" "$SRC/lumen-cert-watch.timer" /etc/systemd/system/
systemctl daemon-reload
if [ "$CERT_WATCH_ON" = 1 ]; then
  systemctl enable --now lumen-cert-watch.timer
  echo "lumen-cert-watch.timer installed; first run at boot+10min, then every 24h"
else
  # wq#5259: intended-off; installing files must not undo the 2026-10-08 subtraction.
  echo "lumen-cert-watch units installed; timer left as-is (intended-off, CERT_WATCH_ON=0)"
fi
echo "dry run:    python3 $DEST/cert_watch.py run --dry-run"
echo "manual run: systemctl start lumen-cert-watch.service"
echo "logs:       journalctl -u lumen-cert-watch.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-cert-watch.timer; rm /etc/systemd/system/lumen-cert-watch.service /etc/systemd/system/lumen-cert-watch.timer; rm -r $DEST"
