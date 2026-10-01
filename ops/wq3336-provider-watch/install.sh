#!/usr/bin/env bash
# install.sh — deploy provider-watch (wq#3336). Requires root (writes
# /etc/systemd/system and reloads the daemon) — run this once, by hand, as an
# operator with root on Lumen. Not runnable from inside an fworker sandbox: no
# --add-dir covers /etc/systemd/system and sudo/systemctl-enable are deny-listed
# verbs there (see DESIGN.md "What's deployed vs. what needs a privileged
# follow-up"). Idempotent: safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DST=/home/lumen/bin/provider-watch
UNIT_DIR=/etc/systemd/system

if [ "$(id -u)" -ne 0 ]; then
  echo "install.sh: must run as root (writes $UNIT_DIR)" >&2
  exit 1
fi

install -m 0755 -o lumen -g lumen "$HERE/provider_watch.py" "$BIN_DST"
install -m 0644 "$HERE/provider-watch.service" "$UNIT_DIR/provider-watch.service"
install -m 0644 "$HERE/provider-watch.timer" "$UNIT_DIR/provider-watch.timer"

systemctl daemon-reload
systemctl enable --now provider-watch.timer

echo "install.sh: installed $BIN_DST, enabled provider-watch.timer"
systemctl list-timers provider-watch.timer --no-pager
