#!/usr/bin/env bash
# p1-update.sh -- apply OS/package updates on ONE P1 Commander node.
#
# MANUAL, FRANK-DRIVEN ONLY. Never add this to a timer or cron: control-plane
# rule 3 (claude_memory.md, 2026-09-25) bans unattended changes to any node
# that is currently part of the live control plane, and rule 2 requires the
# backup to be updated in ITS OWN dedicated window, never the same day as
# primary -- both of those are judgment calls this script cannot make safely
# on its own (it doesn't know whether IT is the currently-active node). Run
# it by hand, over SSH, with a human watching the output:
#   ssh <user>@<ts-ip> 'sudo /home/<user>/bin/p1-update.sh --apply'
# Dry run (default, no --apply): shows what would change, changes nothing.
#
# After a successful --apply run, this prints a UPDATE_OK line. Copy the
# date it prints into the lumen_state.py ingest command it also prints, and
# run THAT from Lumen (this script does not ssh to Lumen or write fleet
# state itself -- it only knows about the box it's running on) so
# p1-update-gate.sh (which runs on Lumen) can compute the 2-week window.
set -euo pipefail

APPLY=0
[ "${1:-}" = --apply ] && APPLY=1

NODE=$(hostname -s)
DATE=$(date -u +%Y-%m-%d)

echo "=== p1-update.sh on $NODE ($([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN)) ==="

if [ "$APPLY" = 1 ]; then
  [ "$(id -u)" = 0 ] || { echo "must run as root for --apply"; exit 1; }
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
  APT_COUNT=$(apt list --upgradable 2>/dev/null | grep -c upgradable || true)
else
  apt-get update
  apt list --upgradable 2>/dev/null | tail -n +2
  echo "(dry run -- nothing applied; re-run with --apply to actually upgrade)"
  exit 0
fi

# npm globals stay PINNED to Lumen's own versions on purpose (parity across
# every control-plane node, whichever one is active) -- do not `npm update -g`
# blindly here. If Lumen's pins change, re-run install.sh's npm line with the
# new version numbers on both P1 nodes deliberately, not via this script.
CUR_SG=$(npm ls -g --depth=0 2>/dev/null | grep -o 'supergateway@[0-9.]*' || echo 'supergateway@?')
CUR_DC=$(npm ls -g --depth=0 2>/dev/null | grep -o '@wonderwhy-er/desktop-commander@[0-9.]*' || echo 'dc@?')

echo
echo "UPDATE_OK date=${DATE} node=${NODE} apt_upgraded=${APT_COUNT:-0} ${CUR_SG} ${CUR_DC}"
echo
echo "Now run FROM LUMEN to record this in fleet state:"
echo "  python3 /home/lumen/minerva/lumen_state.py ingest ${NODE} last_update '${DATE} (apt:${APT_COUNT:-0} pkgs, ${CUR_SG}, ${CUR_DC})' --valid-from ${DATE} --source wq2744-p1-update"
echo "  /home/lumen/bin/state-regen"
echo
# wq#3804: a package update can touch the desktop-commander dist; the running child only picks it up
# after a restart. Report (read-only) whether the redaction code is live. This never restarts anything.
GATE="$(dirname "$(readlink -f "$0")")/dc-redaction-gate.sh"
if [ -x "$GATE" ]; then
  echo "--- dc-redaction-gate (wq#3804) ---"
  "$GATE" || echo "*** DC redaction not confirmed live -- restart dc-gateway (detached, with deadman, Frank present) and re-run: $GATE --probe 3100 ***"
else
  echo "(dc-redaction-gate.sh not found next to p1-update.sh -- run it by hand after any DC change)"
fi
echo

echo "Reboot only if apt reports it's required (/var/run/reboot-required) --"
echo "and only with Frank's explicit go-ahead (control-plane rule + fleet no-reboot-without-permission)."
[ -f /var/run/reboot-required ] && echo "*** REBOOT REQUIRED -- do not reboot the currently-ACTIVE node without failing over first ***"
exit 0
