#!/usr/bin/env bash
# wq#2136 bundle, RETIRED by wq#2157 (see RUNBOOK.md).
#
# The unit this directory ships is harmful (research/ISIS_POWEROFF_RCA.md section 5: +16.6 W idle,
# unbind fails every start, violates the 2026-06-25 standing rule). It is kept only as the reversal
# artifact. There is no install path.
#
# --verify asserts the RETIRED state on hathor and nephthys, read-only:
#   the unit is not enabled, not active, not wanted by multi-user.target, and BOTH D300s are
#   amdgpu-bound. A node that has drifted back toward the disabled-GPU configuration fails.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
NODES="hathor nephthys"
UNIT=disable-gpus.service
WANTS=/etc/systemd/system/multi-user.target.wants
GPUS="0000:02:00.0 0000:06:00.0"

# One round-trip per node; every command is read-only.
#
# Note on `|| echo absent`: do NOT write it that way. `systemctl is-enabled` exits 1 for a
# unit that is *disabled* — the expected state here — so a `||` fallback fires on the happy
# path and appends its word to the real answer. Capture first, default only when empty.
_probe='u='"$UNIT"'; w='"$WANTS"';
        e=$(systemctl is-enabled "$u" 2>/dev/null); [ -n "$e" ] || e=absent;
        a=$(systemctl is-active  "$u" 2>/dev/null); [ -n "$a" ] || a=absent;
        if [ -e "$w/$u" ]; then k=yes; else k=no; fi;
        g=$(ls /sys/bus/pci/drivers/amdgpu/ 2>/dev/null | tr "\n" " ");
        printf "enabled=%s\nactive=%s\nwanted=%s\namdgpu=%s\n" "$e" "$a" "$k" "$g"'

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for n in $NODES; do
    out="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$n" "$_probe" 2>/dev/null)" || {
      echo "ERR cannot probe $n"; _V_ERR=1; continue
    }
    enabled="$(printf '%s\n' "$out" | sed -n 's/^enabled=//p')"
    active="$( printf '%s\n' "$out" | sed -n 's/^active=//p')"
    wanted="$( printf '%s\n' "$out" | sed -n 's/^wanted=//p')"
    amdgpu="$( printf '%s\n' "$out" | sed -n 's/^amdgpu=//p')"

    # Retired means: cannot start at boot. `not-found` (file deleted) is the stronger form and
    # passes; `absent` is this probe's own fallback for an empty reading.
    case "$enabled" in disabled|not-found|absent) ;; *) _vmiss "$n: $UNIT is-enabled=$enabled (want disabled/not-found)";; esac
    case "$active"  in inactive|absent|failed) ;; *) _vmiss "$n: $UNIT is-active=$active (want inactive/absent)";; esac
    [ "$wanted" = no ] || _vmiss "$n: $UNIT still linked in $WANTS"

    # The standing rule: both D300s stay amdgpu-bound (2026-06-25 CRITICAL REVERSAL).
    for g in $GPUS; do
      case " $amdgpu " in *" $g "*) ;; *) _vmiss "$n: $g not bound to amdgpu (GPU disabled?)";; esac
    done
  done
  vdone
fi

echo "wq2136: RETIRED, no install path (see $SRC/RUNBOOK.md). Reversal steps are in the runbook." >&2
exit 3
