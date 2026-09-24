#!/usr/bin/env bash
# Install wq#2428's timer spend guard. Idempotent: re-running converges to the same
# state. Run from a root/interactive session on Lumen — this BUILD worker's sandbox
# has no sudo and cannot install, enable or start a systemd unit (see README.md
# "What the worker could not do").
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# Full-read sweep first: the worker's own run could only read scripts under its
# allowed dirs, so 104 of 171 timers came back `unknown`. As lumen, with every
# ExecStart target readable, --strict is meaningful and this is the real audit.
python3 "$HERE/timer_spend_audit.py" --strict || true
echo

sudo install -m 0644 "$HERE/lumen-timer-spend-guard.service" /etc/systemd/system/lumen-timer-spend-guard.service
sudo install -m 0644 "$HERE/lumen-timer-spend-guard.timer" /etc/systemd/system/lumen-timer-spend-guard.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-timer-spend-guard.timer
systemctl list-timers lumen-timer-spend-guard.timer --no-pager --no-legend

echo
echo "Remaining manual steps (see README.md 'Deploy checklist'):"
echo "  1. Repoint any CLOUD finding from the sweep above at the gateway"
echo "     (ANTHROPIC_BASE_URL=http://localhost:4000 + a local rung), or prove it"
echo "     needs Max and give it a budget check before it calls."
echo "  2. Re-arm the five timers the 95% brake stopped on 2026-09-21:"
echo "     sudo systemctl start lumen-ebay-watch.timer sia-news-pull.timer \\"
echo "       lumen-scout.timer lumen-news-digest.timer motd-news.timer"
echo "     (all five are still 'enabled'; the brake stopped the units, it did not"
echo "      disable them — see README.md 'What actually stopped'.)"
