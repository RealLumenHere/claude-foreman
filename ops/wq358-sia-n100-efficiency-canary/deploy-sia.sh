#!/usr/bin/env bash
# wq#358 — deploy the n100-efficiency pack (wq#357, staged at /home/lumen/n100-efficiency)
# to Sia. NOT run by the fworker BUILD-lane agent that staged this: the installer needs
# root on the target and this harness's guard.sh hard-denies every `sudo` invocation, local
# or remote-embedded, with no exception. This wq358 session's worker-settings.json profile
# has no `ssh` allowlist entry at all (stricter than the wq415/Sobek session that staged
# ops/wq415-sobek-n100-efficiency-canary/), so it could not even read-verify Sia or capture
# a live baseline here — see reports/wq358_sia_n100_efficiency_canary.md and
# kb/ISSUE-LOG.md #39. Run this from a session that isn't fenced that way (Frank
# interactively, or a claude-web session), same as wq415's deploy-sobek.sh.
#
# PRE-REQ: confirm the wq#357 Bes canary soak (48h+, PG standby lag=0, HA/mosquitto
# stable, no NIC drops) actually completed clean before running this. No soak-verification
# report was found in this repo as of this staging (2026-09-02) — wq#357's ticket only
# records the stage+install step, not a soak-close report. Check current Bes state
# (verify-bes equivalent, or PG/HA dashboards) first.
#
# Usage: bash deploy-sia.sh
set -euo pipefail
HOST=sia
SRC=/home/lumen/n100-efficiency

echo "== syncing pack to sia =="
rsync -a "$SRC/" "$HOST:n100-efficiency/"

echo "== installing (root on target) =="
ssh -o BatchMode=yes -o StrictHostKeyChecking=no "$HOST" \
  'sudo bash ~/n100-efficiency/install.sh'

echo "== post-install verify =="
"$(dirname "$0")/verify-sia.sh"

echo "== soak =="
echo "Record this run's UTC start time and diff verify-sia.sh output against"
echo "baseline-sia-pre.md over 24-48h. Watch: unit_active=active, PL1=10000000,"
echo "PL2=15000000, EPP=balance_power, swappiness=10."
echo ""
echo "ASPM decision (Sia is the canary): watch nic_operstate stays 'up', and"
echo "nic_carrier_changes / nic_rx_errors / nic_tx_errors / nic_rx_dropped stay flat"
echo "against baseline over the full soak. Any increase = ASPM broke the RTL8125 again"
echo "(same failure class the ticket warns about) -> rollback and do NOT promote"
echo "ASPM=powersupersave to Bes."
echo ""
echo "Containerized services: confirm 'docker ps' still shows the PG17, miniflux, and"
echo "news-intake containers healthy/running post-install and through the soak (webhook"
echo "path health = news-intake container staying up and not restart-looping)."
echo ""
echo "Rollback: ssh $HOST 'sudo bash ~/n100-efficiency/uninstall.sh'"
