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
SIA=sia@100.111.15.120
WORKDIR=/home/sia/services/rsshub
UNIT=/etc/systemd/system/rsshub.service

# RETIRED 2026-10-07 (wq#3486). The bundle cannot run as shipped: rsshub.service
# starts `node lib/index.js`, which does not exist on upstream DIYgod/RSSHub
# master -- the real entrypoint is dist/index.mjs, built by `npm run build`, a
# step this installer never runs (wq#2505 verify-pass contradiction). Checked on
# Sia 2026-10-07: rsshub.service inactive/not-found, $WORKDIR absent, nothing on
# :1200; Miniflux + news-intake run in docker. Nothing was ever deployed, so
# there is nothing to roll back. The velocity-feed need (github_scout's star
# floor cannot surface new repos) still stands; it needs a fresh bundle against
# current upstream, not this one. Set WQ2500_FORCE_RETIRED_DEPLOY=1 to override.

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
# Inverted for a retired bundle: the broken unit must not have landed on Sia.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if vremote_up "$SIA"; then
    if $_VSSH "$SIA" "test -e $UNIT" 2>/dev/null; then
      _vmiss "retired unit present on Sia: $UNIT (wq#3486)"
    fi
  fi
  vdone
fi

if [ "${WQ2500_FORCE_RETIRED_DEPLOY:-0}" != "1" ]; then
  echo "refuse: wq#2500 rsshub-bridge was RETIRED 2026-10-07 (wq#3486) -- its ExecStart" >&2
  echo "        names an entrypoint upstream no longer ships. See the header." >&2
  exit 2
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
