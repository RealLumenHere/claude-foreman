#!/usr/bin/env bash
# wq#857 — ship this node's journald to VictoriaLogs on Bes, via
# systemd-journal-upload (the systemd-native journald forwarder — chosen over
# a Vector agent per-node: it's an apt package already in Debian/Ubuntu repos,
# no extra long-running Go/Rust binary to track/update per fleet node, and
# VictoriaLogs has a native /insert/journald endpoint built for exactly this
# client, with automatic level/stream-field mapping — see
# docs.victoriametrics.com/victorialogs/data-ingestion/journald/).
#
# Root required (apt install, /etc/systemd/journal-upload.conf, systemctl
# enable). Run this on EACH fleet node as root — see rollout-fleet-shipper.sh
# for the fan-out loop, and README.md for why a fworker BUILD-lane agent can't
# run this itself.
#
# Usage: install-journal-shipper.sh <tailscale-ip-of-bes>
set -euo pipefail

BES_ADDR="${1:?usage: install-journal-shipper.sh <tailscale-ip-of-bes>}"

echo "== installing systemd-journal-remote (provides systemd-journal-upload) =="
if ! systemctl list-unit-files systemd-journal-upload.service >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y systemd-journal-remote
fi

echo "== configuring upload target =="
sed "s/__BES_ADDR__/${BES_ADDR}/g" "$(dirname "$0")/journal-upload.conf.template" \
  > /etc/systemd/journal-upload.conf

echo "== enabling shipper =="
systemctl enable --now systemd-journal-upload.service
sleep 2
systemctl --no-pager status systemd-journal-upload | head -10

echo "== done: $(hostname) journald forwarding to http://${BES_ADDR}:9428/insert/journald =="
echo "Rollback: systemctl disable --now systemd-journal-upload && rm -f /etc/systemd/journal-upload.conf"
