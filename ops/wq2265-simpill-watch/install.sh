#!/usr/bin/env bash
# wq#2265 — install lumen-simpill-watch.{service,timer}. Run as root via
# lumen-run on the target host itself (same convention as
# ops/wq1531-read-dir-sweep/install.sh):
#
#   /home/lumen/bin/lumen-run start --root -- bash \
#       /home/lumen/foreman/ops/wq2265-simpill-watch/install.sh
#
# WHY this exists: wq#2242 landed the units under harness/systemd/ and stopped
# there, so the enabled simpill lane had no producer (wq#2259 verify:
# `systemctl is-enabled lumen-simpill-watch.timer` -> not-found). Same shape as
# wq#1404 / wq#1531. Idempotent: safe to re-run.
set -euo pipefail

SRC=/home/lumen/foreman/harness/systemd
STATE=/home/lumen/foreman/dispatcher/runtime/simpill_seen.json
REPOS=/home/lumen/foreman/harness/simpill_repos.json

if [ "${1:-}" = --verify ]; then
  . "$(cd "$(dirname "$0")" && pwd)/../_verify.sh"
  vunit_file "$SRC/lumen-simpill-watch.service" /etc/systemd/system/lumen-simpill-watch.service
  vunit_file "$SRC/lumen-simpill-watch.timer"   /etc/systemd/system/lumen-simpill-watch.timer
  venabled   lumen-simpill-watch.timer
  vactive    lumen-simpill-watch.timer
  # wq#4910: no vtimer_scheduled for lumen-simpill-watch.timer: intended-off, disabled by the
  # 2026-10-08 subtraction (scratch/subtraction/timers-disable-phase2.txt). Re-enabling it
  # is a decision, not a heal; whoever makes it adds vtimer_scheduled here.
  vcontains  "$STATE" '"baselined": *true'
  vcontains  "$REPOS" '"fork_owner": *"[^"]+"'
  vcontains  "$REPOS" '"us": *\[ *"[^"]+"'
  vdone
fi

install -D -m 644 "$SRC/lumen-simpill-watch.service" /etc/systemd/system/lumen-simpill-watch.service
install -D -m 644 "$SRC/lumen-simpill-watch.timer"   /etc/systemd/system/lumen-simpill-watch.timer
systemctl daemon-reload
systemctl enable --now lumen-simpill-watch.timer

# First run baselines every watched repo (records current events, files nothing).
# Starting the unit rather than python3 directly runs it as User=lumen and
# proves the installed unit itself works.
systemctl start lumen-simpill-watch.service
systemctl is-active --quiet lumen-simpill-watch.timer

echo "lumen-simpill-watch.timer installed and enabled (every 15 min, Persistent)"
ls -l "$STATE"
systemctl list-timers --no-pager lumen-simpill-watch.timer
