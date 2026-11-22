#!/usr/bin/env bash
# wq#901 ntfy OOB 429 fix. Run as root via lumen-run --root on lumen itself
# (repo convention — see ops/wq900-primary-tree-guard, ops/wq859-renovate-gitea).
#
# 1. Drops "high" from NTFY_OOB_PRIORITIES so routine high-priority mirroring
#    stops draining the anonymous ntfy.sh rate budget (design in README.md).
# 2. Installs a daily canary that forces one OOB publish and asserts oob=200,
#    so the now-rarely-exercised path can't rot silently.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CONF=/home/lumen/etc/ntfy-send.conf

# wq#5277: the canary timer is INTENDED-OFF. The 2026-10-08 timer massacre (wq#3648, phase 2)
# disabled it on purpose: /home/lumen/scratch/subtraction/timers-disable-phase2.txt:50 and
# timer-massacre-phase2.md:62 ("Alert path extras: ... ntfy-oob-canary"); not in
# timers-reenable-phase2.txt or the wq#3655 addendum. Asserting enabled+active made --verify
# fail forever and filed an ops-root-apply telling the reader to run this script as root,
# whose `enable --now` + `start` would undo the subtraction. 0 = verify checks the config
# marker and both unit files only, and the install copies the units without enabling or
# firing them. Re-enabling the canary is a decision: whoever makes it sets this to 1, which
# restores venabled/vactive/vtimer_scheduled and the enable --now + first-fire install path.
NTFY_OOB_CANARY_ON=0

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# Two halves, and only one of them landed: the config edit is a marker check,
# the canary is a unit pair.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains  "$CONF" '^NTFY_OOB_PRIORITIES="?urgent"?$'
  vunit_file "$SRC/ntfy-oob-canary.service" /etc/systemd/system/ntfy-oob-canary.service
  vunit_file "$SRC/ntfy-oob-canary.timer"   /etc/systemd/system/ntfy-oob-canary.timer
  if [ "$NTFY_OOB_CANARY_ON" = 1 ]; then
    venabled         ntfy-oob-canary.timer
    vactive          ntfy-oob-canary.timer
    vtimer_scheduled ntfy-oob-canary.timer
  fi
  vdone
fi

grep -q '^NTFY_OOB_PRIORITIES=' "$CONF" || { echo "install: NTFY_OOB_PRIORITIES not in $CONF" >&2; exit 1; }
sed -i 's/^NTFY_OOB_PRIORITIES=.*/NTFY_OOB_PRIORITIES="urgent"/' "$CONF"
echo "conf: $(grep '^NTFY_OOB_PRIORITIES=' "$CONF")"

cp "$SRC/ntfy-oob-canary.service" "$SRC/ntfy-oob-canary.timer" /etc/systemd/system/
systemctl daemon-reload
if [ "$NTFY_OOB_CANARY_ON" != 1 ]; then
  echo "ntfy-oob-canary units installed, timer left disabled (intended-off, wq#5277)"
  exit 0
fi
systemctl enable --now ntfy-oob-canary.timer

# Prove the path once now rather than waiting a day for the first timer fire.
systemctl start ntfy-oob-canary.service

echo "ntfy-oob-canary.timer installed; daily at 09:17 (+0-5min jitter)"
echo "manual run:   systemctl start ntfy-oob-canary.service"
echo "logs:         journalctl -u ntfy-oob-canary.service -n 20 --no-pager"
