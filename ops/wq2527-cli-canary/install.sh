#!/usr/bin/env bash
# wq#2527 — install the cli_canary units. Run as root via lumen-run on Lumen itself, after the
# PR lands (the units run /home/lumen/foreman/dispatcher/cli_canary.py):
#
#   /home/lumen/bin/lumen-run start --root -- bash \
#       /home/lumen/foreman/ops/wq2527-cli-canary/install.sh
#
# lumen-cli-canary.{timer,path}: probe on a 15-min tick, on a models.yaml rewrite, and on an npm
#   change under @anthropic-ai; only missing/stale (cli version, model) pairs cost a call.
# lumen-cli-drift.timer: daily installed-vs-npm-latest check -> one aq row when a live rung
#   needs the newer CLI.
# Idempotent: safe to re-run. Rollback: disable the three triggers; FOREMAN_CLI_CANARY_MODE=off
# in the dispatcher's env releases every hold without touching the units.
set -euo pipefail

SRC=/home/lumen/foreman/harness/systemd
for u in lumen-cli-canary.service lumen-cli-canary.timer lumen-cli-canary.path \
         lumen-cli-drift.service lumen-cli-drift.timer; do
    install -D -m 644 "$SRC/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable --now lumen-cli-canary.timer lumen-cli-canary.path lumen-cli-drift.timer

# Seed the cache and the lumen.claude_code_cli fact now, as User=lumen, through the unit itself.
systemctl start lumen-cli-canary.service
systemctl is-active --quiet lumen-cli-canary.timer
systemctl is-active --quiet lumen-cli-canary.path
systemctl is-active --quiet lumen-cli-drift.timer

echo "cli_canary units installed"
ls -l /home/lumen/foreman/dispatcher/runtime/cli_canary.json
systemctl list-timers --no-pager lumen-cli-canary.timer lumen-cli-drift.timer
