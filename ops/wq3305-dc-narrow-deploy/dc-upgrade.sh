#!/usr/bin/env bash
# wq#3305 — atomic desktop-commander upgrade.
#
# ROOT CAUSE (wq#3305): `npm install -g @wonderwhy-er/desktop-commander@<ver>`
# writes straight into ~/.npm-global, which deletes the old bin/desktop-commander
# symlink before the new one lands. The DC systemd unit has Restart=always with
# no install-time stop, so it crash-loops (~70 restarts, wq#3305: 2026-09-30
# 20:22-20:44 PDT) against the brief "not found" window instead of just waiting
# for the next good start.
#
# FIX: install the new version into a disposable STAGING prefix first (so a
# broken registry push or partial install never touches the live tree), then
# swap the bin symlink in one `ln -sfn` (atomic rename, no window where the
# symlink target is missing) while the unit is stopped for the swap itself.
#
# Usage: dc-upgrade.sh <version> <npm-global-prefix> <systemd-unit>
#   e.g. dc-upgrade.sh 0.2.47 /home/lumen/.npm-global desktop-commander-mcp.service
set -euo pipefail

VERSION="${1:?usage: dc-upgrade.sh <version> <npm-global-prefix> <systemd-unit>}"
PREFIX="${2:?usage: dc-upgrade.sh <version> <npm-global-prefix> <systemd-unit>}"
UNIT="${3:?usage: dc-upgrade.sh <version> <npm-global-prefix> <systemd-unit>}"

STAGING="${PREFIX}/staging-dc-${VERSION}"
LIVE_BIN="${PREFIX}/bin/desktop-commander"

echo "== staging install: @wonderwhy-er/desktop-commander@${VERSION} -> ${STAGING}"
rm -rf "$STAGING"
mkdir -p "$STAGING"
npm install --prefix "$STAGING" "@wonderwhy-er/desktop-commander@${VERSION}"

NEW_BIN="${STAGING}/node_modules/.bin/desktop-commander"
[ -x "$NEW_BIN" ] || { echo "staging install did not produce an executable bin: $NEW_BIN"; exit 1; }

echo "== stopping ${UNIT} for the swap"
sudo systemctl stop "$UNIT"

echo "== atomic symlink swap: ${LIVE_BIN} -> ${NEW_BIN}"
ln -sfn "$NEW_BIN" "$LIVE_BIN"

echo "== starting ${UNIT}"
sudo systemctl start "$UNIT"
sleep 2
sudo systemctl is-active --quiet "$UNIT" || { echo "unit failed to come up after swap"; exit 1; }

echo "== done: $(readlink -f "$LIVE_BIN")"
