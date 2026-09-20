#!/usr/bin/env bash
# version: wq2166-v4
# gpu-lowpower.sh — hold every amdgpu card at power_dpm_force_performance_level=low
# and power_dpm_state=battery.
#
# wq#2166. Replaces two earlier shapes:
#   * hathor ran the knob inline in the unit's ExecStart as
#     `for f in /sys/class/drm/card*/device/...; do echo low > "$f" 2>/dev/null || true; done; exit 0`.
#     When the glob matched nothing bash passed the LITERAL pattern through, every
#     redirection failed, and `|| true` + `exit 0` reported success. hathor sat at
#     dpm=auto from 2026-09-19 to 2026-09-20 with `is-active` green.
#   * nephthys ran v3, which waited for the cards but was still the only ordering
#     mechanism and wrote without reading back.
#
# Two things are non-negotiable here:
#   1. Never act on an unmatched glob. Every loop tests `-e` before it writes.
#   2. Never exit 0 on a card that is not at low. Every write is read back.
#
# Modes:
#   --card cardN   apply to one card, verify it (udev starts one per card)
#   (no args)      wait for EXPECTED_CARDS, apply to all, verify all (boot reconciler)
#   --check        read-only: verify all, write nothing
set -uo pipefail

DRM_ROOT="${DRM_ROOT:-/sys/class/drm}"
EXPECTED_CARDS="${EXPECTED_CARDS:-2}"   # MP6,1 ships two D300s; override in /etc/default/gpu-lowpower
WAIT_SECS="${WAIT_SECS:-120}"           # reconciler backstop only; udev is the ordering mechanism
CARD_WAIT_SECS="${CARD_WAIT_SECS:-10}"  # --card: the knob appears with the card, but not atomically
LEVEL=low
STATE=battery

LVL_ATTR=power_dpm_force_performance_level
ST_ATTR=power_dpm_state

log()  { printf '%s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; }

# cards — names of card dirs that actually expose the dpm knob, one per line.
# The `-e` test is what makes an unmatched glob a no-op instead of a literal path.
cards() {
  local d
  for d in "$DRM_ROOT"/card[0-9]*; do
    [ -e "$d/device/$LVL_ATTR" ] && printf '%s\n' "${d##*/}"
  done
  return 0
}

ncards() { cards | wc -l; }

apply_card() {
  local c="$1" dev="$DRM_ROOT/$1/device" rc=0 i
  for ((i = 0; i < CARD_WAIT_SECS; i++)); do
    [ -e "$dev/$LVL_ATTR" ] && break
    sleep 1
  done
  if [ ! -e "$dev/$LVL_ATTR" ]; then
    fail "$c: no $LVL_ATTR after ${CARD_WAIT_SECS}s (card gone, or not an amdgpu card)"
    return 1
  fi
  printf '%s\n' "$LEVEL" > "$dev/$LVL_ATTR" || { fail "$c: cannot write $LVL_ATTR"; rc=1; }
  # power_dpm_state is SI-era and may be absent on other ASICs; only a present knob is required.
  if [ -e "$dev/$ST_ATTR" ]; then
    printf '%s\n' "$STATE" > "$dev/$ST_ATTR" || { fail "$c: cannot write $ST_ATTR"; rc=1; }
  fi
  return $rc
}

check_card() {
  local c="$1" dev="$DRM_ROOT/$1/device" got rc=0
  got="$(cat "$dev/$LVL_ATTR" 2>/dev/null)"
  log "$c: $LVL_ATTR=${got:-<unreadable>}"
  [ "$got" = "$LEVEL" ] || { fail "$c: $LVL_ATTR=${got:-<unreadable>} want $LEVEL"; rc=1; }
  if [ -e "$dev/$ST_ATTR" ]; then
    got="$(cat "$dev/$ST_ATTR" 2>/dev/null)"
    log "$c: $ST_ATTR=${got:-<unreadable>}"
    [ "$got" = "$STATE" ] || { fail "$c: $ST_ATTR=${got:-<unreadable>} want $STATE"; rc=1; }
  fi
  return $rc
}

# wait_for_cards — bounded and condition-based. This is a BACKSTOP, not the ordering
# mechanism: 99-gpu-lowpower.rules starts gpu-lowpower@cardN.service the moment each
# card appears. On a node without the rule this loop is what keeps the reconciler
# correct, and it fails loudly when it expires instead of pretending.
wait_for_cards() {
  local i
  for ((i = 0; i < WAIT_SECS; i++)); do
    [ "$(ncards)" -ge "$EXPECTED_CARDS" ] && return 0
    sleep 1
  done
  return 1
}

main() {
  local rc=0 c n=0
  case "${1:-}" in
    --card)
      c="${2:-}"
      [ -n "$c" ] || { fail "--card needs a card name (e.g. --card card1)"; exit 2; }
      apply_card "$c" || rc=1
      check_card "$c" || rc=1
      exit $rc
      ;;
    --check)
      while read -r c; do
        check_card "$c" || rc=1
        n=$((n + 1))
      done < <(cards)
      [ "$n" -ge "$EXPECTED_CARDS" ] || { fail "found $n cards, expected $EXPECTED_CARDS"; rc=1; }
      exit $rc
      ;;
    "")
      wait_for_cards || { fail "only $(ncards)/$EXPECTED_CARDS amdgpu cards after ${WAIT_SECS}s"; rc=1; }
      while read -r c; do
        apply_card "$c" || rc=1
        check_card "$c" || rc=1
        n=$((n + 1))
      done < <(cards)
      [ "$n" -ge "$EXPECTED_CARDS" ] || { fail "applied to $n cards, expected $EXPECTED_CARDS"; rc=1; }
      exit $rc
      ;;
    *)
      fail "unknown argument: $1 (want --card cardN, --check, or no argument)"
      exit 2
      ;;
  esac
}

main "$@"
