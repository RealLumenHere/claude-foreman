#!/usr/bin/env bash
# wq#1705 rollout — install guard-meta-watchdog (the remediation half that
# `lumen-failed-units` deliberately does not do). Run AS ROOT.
#
# Installs the sweeper + its fallback finding-exit list, then enables a 10m
# timer. Deliberately does NOT start the service in-line: the first run should
# be a --dry-run an operator reads, because this is the one fleet tool allowed
# to restart units on its own.
#
# Rollback:
#   systemctl disable --now lumen-guard-meta-watchdog.timer
#   rm -f /etc/systemd/system/lumen-guard-meta-watchdog.{service,timer}
#   rm -f /home/lumen/bin/guard_meta_watchdog.py
#   rm -rf /home/lumen/agents/guard-meta-watchdog
#   systemctl daemon-reload
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/home/lumen/bin
STATE_DIR=/home/lumen/agents/guard-meta-watchdog

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# The timer is deliberately enabled-but-not-started by install.sh (the first run
# should be a dry-run an operator reads), so enablement is the live signal here,
# not activity.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile      "$HERE/staged/guard_meta_watchdog.py"  "$BIN/guard_meta_watchdog.py"
  # wq#1657 owns this list and wins when present, so ours is a fallback the
  # installer skips. Presence is the contract; byte-equality would be a false MISS.
  [ -e "$BIN/guard-finding-exit.list" ] || _vmiss "absent: $BIN/guard-finding-exit.list"
  vunit_file "$HERE/staged/lumen-guard-meta-watchdog.service" \
             /etc/systemd/system/lumen-guard-meta-watchdog.service
  vunit_file "$HERE/staged/lumen-guard-meta-watchdog.timer" \
             /etc/systemd/system/lumen-guard-meta-watchdog.timer
  venabled   lumen-guard-meta-watchdog.timer
  vdone
fi

[ "$(id -u)" = 0 ] || { echo "must run as root (unit install + enable)"; exit 1; }

python3 "$HERE/test_guard_meta_watchdog.py" >/dev/null 2>&1 \
  || { echo "REFUSING: test suite does not pass on this checkout"; exit 1; }

install -o lumen -g lumen -m 0755 "$HERE/staged/guard_meta_watchdog.py" \
  "$BIN/guard_meta_watchdog.py"
# Fallback only: wq#1657 owns $BIN/guard-finding-exit.list and wins when present,
# so never clobber a list that ticket already deployed.
if [ ! -e "$BIN/guard-finding-exit.list" ]; then
  install -o lumen -g lumen -m 0644 "$HERE/staged/guard-finding-exit.list" \
    "$BIN/guard-finding-exit.list"
fi
install -d -o lumen -g lumen -m 0755 "$STATE_DIR"

install -m 0644 "$HERE/staged/lumen-guard-meta-watchdog.service" \
  /etc/systemd/system/lumen-guard-meta-watchdog.service
install -m 0644 "$HERE/staged/lumen-guard-meta-watchdog.timer" \
  /etc/systemd/system/lumen-guard-meta-watchdog.timer

systemctl daemon-reload
systemctl enable lumen-guard-meta-watchdog.timer
systemctl start lumen-guard-meta-watchdog.timer

echo "installed. Read what it would do before trusting it to act:"
echo "  python3 $BIN/guard_meta_watchdog.py --dry-run"
echo "  systemctl list-timers lumen-guard-meta-watchdog.timer"
