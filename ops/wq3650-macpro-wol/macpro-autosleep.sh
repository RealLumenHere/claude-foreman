#!/usr/bin/env bash
# ops/wq3650-macpro-wol/macpro-autosleep.sh — wq#3650: sleep the box if the 1-minute load
# average has stayed under LOW_LOAD for WINDOW_MIN minutes straight. Run every CHECK_INTERVAL_MIN
# minutes by macpro-autosleep.timer; a streak counter in STATE_FILE survives between runs (and is
# naturally reset by a reboot, which is the correct behaviour — a freshly-woken box should not
# inherit a near-complete streak from before it last slept).
#
# This is the literal "load < 0.2 for 30min" ask (wq#3650). It is deliberately simpler, and a
# different mechanism, from dispatcher/node_power.py's own idle-drain (which counts in-flight
# units and ready tickets, not load average) — that logic only ever runs for a registry node
# whose power_policy is "cold"; isis and hathor are "warm"/"always-on" by Frank's standing
# decision (research/inference_nodes.yaml), so this host-local cron is the only idle-power
# mechanism active on them today. See README.md "Why both exist" before changing either.
#
# The Apple EFI WoL quirk (ticket wq#3650) is why this ALWAYS uses a clean `systemctl poweroff`
# and never cuts mains power itself: WoL only survives a clean shutdown, not an AC loss.
set -uo pipefail

LOW_LOAD="${MACPRO_AUTOSLEEP_LOW_LOAD:-0.2}"
WINDOW_MIN="${MACPRO_AUTOSLEEP_WINDOW_MIN:-30}"
CHECK_INTERVAL_MIN="${MACPRO_AUTOSLEEP_CHECK_INTERVAL_MIN:-5}"
STATE_FILE="${MACPRO_AUTOSLEEP_STATE_FILE:-/var/lib/macpro-autosleep/streak}"
NEED_STREAK=$(( (WINDOW_MIN + CHECK_INTERVAL_MIN - 1) / CHECK_INTERVAL_MIN ))

load1="$(cut -d' ' -f1 /proc/loadavg)"
mkdir -p "$(dirname "$STATE_FILE")"
streak=0
[ -f "$STATE_FILE" ] && streak="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
case "$streak" in ''|*[!0-9]*) streak=0 ;; esac

below=$(awk -v l="$load1" -v t="$LOW_LOAD" 'BEGIN{print (l < t) ? 1 : 0}')
if [ "$below" = 1 ]; then
  streak=$((streak + 1))
else
  streak=0
fi
echo "$streak" > "$STATE_FILE"

printf 'load1=%s threshold=%s streak=%s/%s\n' "$load1" "$LOW_LOAD" "$streak" "$NEED_STREAK"

if [ "$streak" -ge "$NEED_STREAK" ]; then
  echo "0" > "$STATE_FILE"     # reset: a wake should not immediately re-arm a near-complete streak
  echo "load under $LOW_LOAD for ~${WINDOW_MIN}min — sleeping (clean poweroff, wq#3650)"
  exec systemctl poweroff
fi
