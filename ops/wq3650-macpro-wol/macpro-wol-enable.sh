#!/usr/bin/env bash
# ops/wq3650-macpro-wol/macpro-wol-enable.sh — set ethtool Wake-on-LAN to `g` (magic packet) on
# the fleet LAN NIC and read it back. Never exits 0 on a card that isn't actually armed, same
# discipline as ops/wq2166-gpu-lowpower-ordering/gpu-lowpower.sh.
#
# isis and hathor both use enp12s0 for the fleet LAN (research/INVENTORY_RECONCILIATION.md:117,127);
# override with MACPRO_WOL_IFACE in /etc/default/macpro-wol if that ever drifts.
#
# Apple EFI WoL quirk (ticket wq#3650, Frank's research docs): after a HARD power loss (AC cut,
# not a clean shutdown) these Mac Pros come back up with WoL disabled in EFI and ethtool's `wol g`
# alone does not restore it — the box needs one full clean boot-then-`systemctl poweroff` cycle
# before a magic packet will wake it again. This script re-arms the OS-level driver flag on every
# boot; it cannot repair the EFI-level loss, which is why dispatcher/node_power.py's drain path
# (wq#3650) always uses a clean `systemctl poweroff` and never cuts AC on a wol-method node
# (dispatcher/node_power.py power_down(), step 6).
#
# --check: read-only, verify wol=g, write nothing.
set -uo pipefail
IFACE="${MACPRO_WOL_IFACE:-enp12s0}"

fail() { printf 'FAIL: %s\n' "$*" >&2; }

check() {
  local got
  got="$(ethtool "$IFACE" 2>/dev/null | awk -F': ' '/Wake-on/{print $2}')"
  printf '%s: Wake-on=%s\n' "$IFACE" "${got:-<unreadable>}"
  [ "$got" = "g" ]
}

if [ "${1:-}" = --check ]; then
  check || { fail "$IFACE: Wake-on is not 'g'"; exit 1; }
  exit 0
fi

command -v ethtool >/dev/null || { fail "ethtool not installed"; exit 1; }
ip link show "$IFACE" >/dev/null 2>&1 || { fail "no such interface: $IFACE"; exit 1; }
ethtool -s "$IFACE" wol g || { fail "ethtool -s $IFACE wol g refused"; exit 1; }
check || { fail "$IFACE: wol g did not take"; exit 1; }
