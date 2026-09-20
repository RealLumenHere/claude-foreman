#!/usr/bin/env bash
# wq#1739: mask lumen-split-watch.timer once split_watch.py has self-retired.
# Needs root (systemctl mask is on the worker deny-list, guard.sh:92-93).
# Run from any other node/lane that may mutate units:
#   ssh lumen@100.117.66.102 'sudo bash /home/lumen/foreman/ops/wq1739-apply-wq1643-split-watch/disable-timer.sh'
set -euo pipefail

STATE=/home/lumen/agents/split-watch/state.json
UNIT=lumen-split-watch.timer

python3 -c "import json,sys; sys.exit(0 if json.load(open('$STATE')).get('retired') else 1)" \
  || { echo "refusing: $STATE does not say retired:true" >&2; exit 1; }

systemctl disable --now "$UNIT"
systemctl mask "$UNIT"
systemctl is-enabled "$UNIT" || true
