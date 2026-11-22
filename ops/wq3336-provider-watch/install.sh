#!/usr/bin/env bash
# install.sh — deploy provider-watch (wq#3336). Requires root (writes
# /etc/systemd/system and reloads the daemon) — run this once, by hand, as an
# operator with root on Lumen. Not runnable from inside an fworker sandbox: no
# --add-dir covers /etc/systemd/system and sudo/systemctl-enable are deny-listed
# verbs there (see DESIGN.md "What's deployed vs. what needs a privileged
# follow-up"). Idempotent: safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DST=/home/lumen/bin/provider-watch
UNIT_DIR=/etc/systemd/system

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vunit_file "$HERE/provider-watch.service" "$UNIT_DIR/provider-watch.service"
  vunit_file "$HERE/provider-watch.timer" "$UNIT_DIR/provider-watch.timer"
  # wq#5273: provider-watch.timer is intended-off. The 2026-10-08 Timer Massacre (wq#3648)
  # disabled it on purpose: /home/lumen/scratch/subtraction/timers-disable-phase2.txt:51,
  # timer-massacre-phase2.md:64 ("Cost/provider"), phase2b-results.txt:52 confirms
  # inactive/disabled. Asserting venabled/vactive made --verify fail forever and filed an
  # ops-root-apply telling the reader to run this script as root, whose
  # `systemctl enable --now` would undo the subtraction. So the timer's live state picks
  # the contract:
  #   off (disabled + inactive) -> the intended state. The timer is the payload's only
  #     caller, so a stale $BIN_DST is dormant: reported as an OFF line, not a MISS. (It is
  #     stale today: wq#3991's RE-upstream sources never reached /home/lumen/bin; the
  #     re-enable below deploys them.)
  #   disabled but active -> MISS: the subtraction was bypassed by hand.
  #   enabled -> someone re-enabled it (a decision): enforce the full contract, payload
  #     byte-match + enabled + active + actually scheduled (wq#4732).
  _pw_en="$(systemctl is-enabled provider-watch.timer 2>/dev/null || true)"
  _pw_act="$(systemctl is-active provider-watch.timer 2>/dev/null || true)"
  case "$_pw_en" in
    enabled|static|alias)
      vfile            "$HERE/provider_watch.py" "$BIN_DST"
      vactive          provider-watch.timer
      vtimer_scheduled provider-watch.timer
      ;;
    *)
      case "$_pw_act" in
        active|activating)
          _vmiss "provider-watch.timer ${_pw_en:-unknown} but active (2026-10-08 subtraction bypassed)" ;;
        *)
          echo "OFF provider-watch.timer ${_pw_en:-unknown}/${_pw_act:-unknown} (intended-off, 2026-10-08 subtraction wq#3648)"
          if [ ! -e "$BIN_DST" ]; then
            echo "OFF dormant payload absent: $BIN_DST"
          elif ! cmp -s "$HERE/provider_watch.py" "$BIN_DST" && ! _vsuperseded "$HERE/provider_watch.py" "$BIN_DST"; then
            echo "OFF dormant payload differs: $BIN_DST (from provider_watch.py; no caller while the timer is off)"
          fi
          ;;
      esac
      ;;
  esac
  vdone
fi

# wq#5273: installing enables provider-watch.timer, reversing the 2026-10-08 subtraction
# (wq#3648). That is a decision, not a heal: refuse unless explicitly asked for.
if [ "${PROVIDER_WATCH_REENABLE:-0}" != 1 ]; then
  echo "install.sh: provider-watch.timer is intended-off (2026-10-08 subtraction," \
       "/home/lumen/scratch/subtraction/timers-disable-phase2.txt:51). Re-enabling is a" \
       "decision; set PROVIDER_WATCH_REENABLE=1 to install anyway." >&2
  exit 2
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "install.sh: must run as root (writes $UNIT_DIR)" >&2
  exit 1
fi

install -m 0755 -o lumen -g lumen "$HERE/provider_watch.py" "$BIN_DST"
install -m 0644 "$HERE/provider-watch.service" "$UNIT_DIR/provider-watch.service"
install -m 0644 "$HERE/provider-watch.timer" "$UNIT_DIR/provider-watch.timer"

systemctl daemon-reload
systemctl enable --now provider-watch.timer

echo "install.sh: installed $BIN_DST, enabled provider-watch.timer"
systemctl list-timers provider-watch.timer --no-pager
