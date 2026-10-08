#!/usr/bin/env bash
# ops/wq3650-macpro-wol/macpro-no-turbo.sh — disable Intel Turbo Boost on the E5-2697 v2
# (research/INVENTORY_RECONCILIATION.md:113). Two independent kernel knobs exist depending on
# the active cpufreq driver; this writes whichever is present and fails loudly if neither is,
# same discipline as ops/wq2166-gpu-lowpower-ordering/gpu-lowpower.sh (never silently no-op).
#
#   intel_pstate: /sys/devices/system/cpu/intel_pstate/no_turbo   1 = disabled
#   acpi-cpufreq: /sys/devices/system/cpu/cpufreq/boost           0 = disabled
#
# --check: read-only, write nothing.
set -uo pipefail

PSTATE=/sys/devices/system/cpu/intel_pstate/no_turbo
BOOST=/sys/devices/system/cpu/cpufreq/boost

fail() { printf 'FAIL: %s\n' "$*" >&2; }

check() {
  local rc=1
  if [ -e "$PSTATE" ]; then
    printf 'intel_pstate/no_turbo=%s\n' "$(cat "$PSTATE" 2>/dev/null)"
    [ "$(cat "$PSTATE" 2>/dev/null)" = "1" ] && rc=0
  fi
  if [ -e "$BOOST" ]; then
    printf 'cpufreq/boost=%s\n' "$(cat "$BOOST" 2>/dev/null)"
    [ "$(cat "$BOOST" 2>/dev/null)" = "0" ] && rc=0
  fi
  return $rc
}

if [ "${1:-}" = --check ]; then
  [ -e "$PSTATE" ] || [ -e "$BOOST" ] || { fail "neither $PSTATE nor $BOOST exists"; exit 1; }
  check || { fail "turbo is not disabled"; exit 1; }
  exit 0
fi

wrote=0
if [ -e "$PSTATE" ]; then
  echo 1 > "$PSTATE" && wrote=1 || fail "cannot write $PSTATE"
fi
if [ -e "$BOOST" ]; then
  echo 0 > "$BOOST" && wrote=1 || fail "cannot write $BOOST"
fi
[ "$wrote" = 1 ] || { fail "neither $PSTATE nor $BOOST exists on this box"; exit 1; }
check || { fail "write did not take"; exit 1; }
