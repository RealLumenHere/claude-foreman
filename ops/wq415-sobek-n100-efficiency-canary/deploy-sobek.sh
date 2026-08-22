#!/usr/bin/env bash
# wq#415 — deploy the n100-efficiency pack (wq#357, staged at /home/lumen/n100-efficiency)
# to Sobek. NOT run by the fworker BUILD-lane agent that staged this: the installer needs
# root on the target and this harness's guard.sh hard-denies every `sudo` invocation, local
# or remote-embedded, with no exception (worker-settings.json deny list + guard.sh regex;
# see reports/wq415_sobek_n100_efficiency_canary.md for why). Run this from a session that
# isn't fenced that way (Frank interactively, or a claude-web session).
#
# Usage: bash deploy-sobek.sh
set -euo pipefail
HOST=sobek@100.86.156.91
SRC=/home/lumen/n100-efficiency

echo "== syncing pack to sobek =="
rsync -a "$SRC/" "$HOST:n100-efficiency/"

echo "== installing (root on target) =="
ssh -o BatchMode=yes -o StrictHostKeyChecking=no "$HOST" \
  'sudo bash ~/n100-efficiency/install.sh'

echo "== post-install verify =="
"$(dirname "$0")/verify-sobek.sh"

echo "== soak =="
echo "Record this run's UTC start time and diff verify-sobek.sh output against"
echo "baseline-sobek-pre.md over 24-48h. Watch: unit_active=active, PL1=10000000,"
echo "PL2=15000000, EPP=balance_power, swappiness=10, the three protected PCI"
echo "power/control values stay 'on', pg_active stays active, walreceiver keeps"
echo "streaming (LSN advancing), pg_journal_errors_last200 stays 0."
echo "Rollback: ssh $HOST 'sudo bash ~/n100-efficiency/uninstall.sh'"
