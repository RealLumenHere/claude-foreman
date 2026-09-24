#!/usr/bin/env bash
# wq#2500 RSSHub install. Run as root on Sia (100.111.15.120) by an on-host operator —
# the BUILD-lane worker that wrote this bundle has no ssh grant to Sia at all
# (kb/ISSUE-LOG.md #39, same wall wq#358 hit), so it cannot run this step itself.
#
# WORKDIR assumes the ssh user `sia` (confirmed by bin/sia-news-pull:6) has home
# /home/sia. Confirm with `echo $HOME` on Sia before running; edit WORKDIR below and
# in rsshub.service if it differs — this was never live-verified from the worktree.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
WORKDIR=/home/sia/services/rsshub
UNIT=/etc/systemd/system/rsshub.service

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/rsshub.service" "$UNIT"
  venabled rsshub.service
  vactive rsshub.service
  [ -d "$WORKDIR/node_modules" ] || _vmiss "absent: $WORKDIR/node_modules (npm ci never ran)"
  [ -r "$WORKDIR/.env" ] || _vmiss "absent: $WORKDIR/.env (copy rsshub.env.example and fill in)"
  vdone
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "install.sh must run as root (systemd unit install + enable)" >&2
  exit 1
fi

mkdir -p "$WORKDIR"
if [ ! -d "$WORKDIR/.git" ]; then
  git clone --depth 1 https://github.com/DIYgod/RSSHub.git "$WORKDIR"
fi
( cd "$WORKDIR" && npm ci --omit=dev )

if [ ! -f "$WORKDIR/.env" ]; then
  cp "$SRC/rsshub.env.example" "$WORKDIR/.env"
  chmod 0600 "$WORKDIR/.env"
  chown sia:sia "$WORKDIR/.env"
  echo "wrote $WORKDIR/.env from template — fill in before the service is useful"
fi
chown -R sia:sia "$WORKDIR"

cp "$SRC/rsshub.service" "$UNIT"
systemctl daemon-reload
systemctl enable --now rsshub.service

echo "rsshub.service installed, bound to 127.0.0.1:1200 (LISTEN_INADDR_ANY=0)"
echo "manual check: curl -s http://127.0.0.1:1200/github/trending/daily/python | head -c 200"
echo "logs:         journalctl -u rsshub.service -n 100 --no-pager"
echo "next:         python3 $SRC/miniflux_subscribe.py  (from a box that can reach Miniflux's API)"
