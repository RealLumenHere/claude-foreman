#!/usr/bin/env bash
# wq#857 — top-level deploy: VictoriaLogs binary + Grafana datasource on Bes,
# then Bes ships its own journald to itself, then the rest of the fleet ships
# to Bes. NOT run by the fworker BUILD-lane agent that staged this: every step
# needs root on a target host, and this harness's guard.sh unconditionally
# denies `sudo` (local or remote-embedded), with no exception — see README.md
# for the full explanation (same constraint wq#415's Sobek canary hit first).
# Run this from Frank interactively or a claude-web session.
#
# Usage: deploy-bes.sh
set -euo pipefail

BES_HOST="bes"                # ~/.ssh/fleet_config alias
BES_ADDR="100.110.156.91"     # Bes tailscale IP — confirmed reachable this session (Grafana :3000 -> 200)
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "== staging files on Bes =="
ssh -o BatchMode=yes "$BES_HOST" "mkdir -p /tmp/wq857-victorialogs"
rsync -a \
  "$SELF_DIR/install-victorialogs.sh" \
  "$SELF_DIR/victorialogs.service.template" \
  "$SELF_DIR/install-grafana-datasource.sh" \
  "$SELF_DIR/grafana-datasource-victorialogs.yaml.template" \
  "$SELF_DIR/install-journal-shipper.sh" \
  "$SELF_DIR/journal-upload.conf.template" \
  "${BES_HOST}:/tmp/wq857-victorialogs/"

echo "== installing VictoriaLogs binary + service =="
ssh -o BatchMode=yes "$BES_HOST" "sudo bash /tmp/wq857-victorialogs/install-victorialogs.sh ${BES_ADDR}"

echo "== installing Grafana datasource =="
ssh -o BatchMode=yes "$BES_HOST" "sudo bash /tmp/wq857-victorialogs/install-grafana-datasource.sh ${BES_ADDR}"

echo "== Bes ships its own journald too (via its tailnet address, same path as every other node) =="
ssh -o BatchMode=yes "$BES_HOST" "sudo bash /tmp/wq857-victorialogs/install-journal-shipper.sh ${BES_ADDR}"

echo "== post-install verify =="
"$SELF_DIR/verify-victorialogs.sh" "$BES_ADDR"

echo "== Bes side done. Next: rollout-fleet-shipper.sh ${BES_ADDR} to cover the rest of the fleet. =="
