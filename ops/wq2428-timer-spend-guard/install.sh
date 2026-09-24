#!/usr/bin/env bash
# Install wq#2428's timer spend guard. Idempotent: re-running converges to the same
# state. Run from a root/interactive session on Lumen — this BUILD worker's sandbox
# has no sudo and cannot install, enable or start a systemd unit (see README.md
# "What the worker could not do").
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# Sweep first, so the install lands against a current picture rather than a remembered
# one. The full-read run is not a privileged step (wq#2456, README "What a worker can
# and cannot do") — it needs only that no --roots is passed. Everything below it is.
python3 "$HERE/timer_spend_audit.py" --strict || true
echo

sudo install -m 0644 "$HERE/lumen-timer-spend-guard.service" /etc/systemd/system/lumen-timer-spend-guard.service
sudo install -m 0644 "$HERE/lumen-timer-spend-guard.timer" /etc/systemd/system/lumen-timer-spend-guard.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-timer-spend-guard.timer
systemctl list-timers lumen-timer-spend-guard.timer --no-pager --no-legend

echo
echo "Remaining manual steps (see README.md 'Deploy checklist'):"
echo "  1. Repoint the four CLOUD units the 2026-09-24 sweep named:"
echo "     lumen-intel, lumen-signal-engine and lumen-worklog call claude with no"
echo "     --model, so they take the account default off Max. A drop-in alone will"
echo "     NOT fix them -- the model is an argv flag, not an env var, so each"
echo "     script's argv needs --model deepseek-v4-flash as well as the"
echo "     ANTHROPIC_BASE_URL=http://localhost:4000 drop-in (key via EnvironmentFile,"
echo "     never inlined). minerva-factory may already be pinned inside its own"
echo "     EnvironmentFile, which this audit will not open -- check before changing."
echo "  2. Re-arm the five timers the 95% brake stopped on 2026-09-21:"
echo "     sudo systemctl start lumen-ebay-watch.timer sia-news-pull.timer \\"
echo "       lumen-scout.timer lumen-news-digest.timer motd-news.timer"
echo "     (all five are still 'enabled'; the brake stopped the units, it did not"
echo "      disable them, and all five audit clean -- see README.md 'What the"
echo "      2026-09-21 brake actually stopped'. Re-confirmed enabled+inactive"
echo "      2026-09-24.)"
