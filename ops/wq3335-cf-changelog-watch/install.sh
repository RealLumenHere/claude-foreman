#!/usr/bin/env bash
# wq#3335 cf-changelog-watch install — run AS ROOT on the target node, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq3335-cf-changelog-watch/install.sh
# (this repo's convention for privileged unit installs — see ops/wq1397-gitea-version-check,
# ops/wq2092-cert-watch.)
# Rollback: systemctl disable --now cf-changelog-watch.timer; rm the installed
# files below; rm the state file (/home/lumen/logs/.cf-changelog-watch.state.json).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/bin/cf-changelog-watch

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/cf_changelog_watch.py" "$DEST"
  vunit_file "$SRC/cf-changelog-watch.service" /etc/systemd/system/cf-changelog-watch.service
  vunit_file "$SRC/cf-changelog-watch.timer" /etc/systemd/system/cf-changelog-watch.timer
  vactive cf-changelog-watch.timer
  venabled cf-changelog-watch.timer
  vdone
fi

install -o lumen -g lumen -m 755 "$SRC/cf_changelog_watch.py" "$DEST"
cp "$SRC/cf-changelog-watch.service" "$SRC/cf-changelog-watch.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cf-changelog-watch.timer

echo "cf-changelog-watch.timer installed; first run at boot+15min, then every 24h"
echo "NOTE: the first real run seeds every currently-matching entry as a baseline"
echo "      WITHOUT filing tickets (the live changelog RSS alone carries ~760"
echo "      historical matches) — only items that appear in later runs alert."
echo "dry run:    $DEST run --dry-run"
echo "manual run: systemctl start cf-changelog-watch.service"
echo "logs:       journalctl -u cf-changelog-watch.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now cf-changelog-watch.timer; rm /etc/systemd/system/cf-changelog-watch.service /etc/systemd/system/cf-changelog-watch.timer; rm $DEST"
