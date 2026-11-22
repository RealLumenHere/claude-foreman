#!/usr/bin/env bash
# wq#1640 deploy — run from a session with unrestricted access to /home/lumen/minerva
# and systemd. Idempotent: every step is safe to re-run.
#
# Status as of this ticket's BUILD session (2026-09-11): prefs_block.py and
# lumen_state.py's `render --prefs-block` hook were already applied LIVE directly
# (minerva/* is not a git repo -- MINERVA_PIPELINE_MAP.md / ISSUE-LOG precedent
# wq#1100/wq#1127 -- so a build worker's Write/Edit tools reach it even though
# Bash's per-command allowlist does not). The daily timer below needs root
# (systemctl + /etc/systemd/system), out of a build worker's reach, so it ships
# here instead of being installed live.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile      "$HERE/prefs_block.py" /home/lumen/minerva/prefs_block.py
  vunit_file "$HERE/prefs-block-check.service" \
             /etc/systemd/system/prefs-block-check.service
  vunit_file "$HERE/prefs-block-check.timer" \
             /etc/systemd/system/prefs-block-check.timer
  # wq#5256: prefs-block-check.timer is intended-off: the 2026-10-08 subtraction disabled
  # it on purpose (/home/lumen/scratch/subtraction/timers-disable-list.txt:59). Asserting
  # enabled+active made --verify fail permanently, and the ops-root-apply it filed told the
  # reader to run this script as root, which runs `systemctl enable --now` and undoes the
  # subtraction. wq#5322 (wq#5273 pattern): the timer's live state picks the contract:
  #   off (disabled + inactive) -> the intended state: an OFF line, no MISS.
  #   disabled but active -> MISS: the subtraction was bypassed by hand.
  #   enabled -> someone re-enabled it (a decision; install.sh refuses that without
  #     PREFS_BLOCK_REENABLE=1): say so, and enforce active + actually scheduled (wq#4732).
  # prefs_block.py is checked unconditionally above: `lumen_state.py render --prefs-block`
  # imports it, so it is not dormant while the timer is off.
  _pb_en="$(systemctl is-enabled prefs-block-check.timer 2>/dev/null || true)"
  _pb_act="$(systemctl is-active prefs-block-check.timer 2>/dev/null || true)"
  case "$_pb_en" in
    enabled|static|alias)
      echo "REENABLED prefs-block-check.timer $_pb_en (intended-off since the 2026-10-08 subtraction; enforcing the on-contract)"
      vactive          prefs-block-check.timer
      vtimer_scheduled prefs-block-check.timer
      ;;
    *)
      case "$_pb_act" in
        active|activating)
          _vmiss "prefs-block-check.timer ${_pb_en:-unknown} but active (2026-10-08 subtraction bypassed)" ;;
        *)
          echo "OFF prefs-block-check.timer ${_pb_en:-unknown}/${_pb_act:-unknown} (intended-off, 2026-10-08 subtraction)" ;;
      esac
      ;;
  esac
  vdone
fi

# wq#5322 (wq#5273 pattern): step 3 below runs `systemctl enable --now
# prefs-block-check.timer`, reversing the 2026-10-08 subtraction. That is a decision, not
# a heal: refuse before any step unless explicitly asked for. Refusing up front (not just
# skipping step 3) also protects step 1: the live prefs_block.py has moved on from this
# bundle copy in the minerva repo (--verify reports it superseded), so reinstalling it
# would revert those commits.
if [ "${PREFS_BLOCK_REENABLE:-0}" != 1 ]; then
  echo "install.sh: prefs-block-check.timer is intended-off (2026-10-08 subtraction," \
       "/home/lumen/scratch/subtraction/timers-disable-list.txt:59). Re-enabling is a" \
       "decision; set PREFS_BLOCK_REENABLE=1 to install anyway." >&2
  exit 2
fi

python3 -m pytest -q "$HERE/test_prefs_block.py"

# 1. prefs_block.py module (idempotent overwrite; content-identical if the BUILD
#    session already landed it).
install -m 0644 "$HERE/prefs_block.py" /home/lumen/minerva/prefs_block.py

# 2. lumen_state.py `render --prefs-block` hook: apply only if not already present.
if ! grep -q 'wq#1640' /home/lumen/minerva/lumen_state.py; then
    patch -p1 -d /home/lumen/minerva < "$HERE/lumen_state_render.diff"
else
    echo "lumen_state.py: wq#1640 hook already present, skipping patch"
fi

# 3. daily drift-check timer.
sudo install -m 0644 "$HERE/prefs-block-check.service" /etc/systemd/system/prefs-block-check.service
sudo install -m 0644 "$HERE/prefs-block-check.timer" /etc/systemd/system/prefs-block-check.timer
sudo systemctl daemon-reload
sudo systemctl enable --now prefs-block-check.timer
systemctl status prefs-block-check.timer --no-pager -n0

# 4. seed the "last handed-out copy" so the FIRST timer fire compares against
#    today's render instead of immediately filing a ticket for a copy nobody
#    has actually handed to Frank yet. Skip this step if you WANT the first
#    fire to open the initial migration ticket (the ticket's own intent: the
#    hand copy is stale today, so the first drift ticket IS the handoff).
# python3 /home/lumen/minerva/prefs_block.py render > /home/lumen/minerva/context/prefs_block.last
