#!/usr/bin/env bash
# wq#2842: lift the fleet low-power clamp (wq#2166) on the card under test, and put it back.
# Sourced by guarded_run.sh, never executed. lib_common.sh must be sourced first (log()).
#
# Grading at power_dpm_force_performance_level=low / power_dpm_state=battery grades idle clocks.
# Both knobs move, because on SI parts power_dpm_state=battery biases state selection even
# when the level is auto. Restore target is the FLEET CONTRACT (low/battery), not "whatever
# it was before": a card found unclamped by a bug must not be restored to the bug.
DRM_ROOT="${DRM_ROOT:-/sys/class/drm}"
GPU_HOLD="${GPU_HOLD:-1}"
HOLD_LEVEL="${HOLD_LEVEL:-auto}"             # auto | high
HOLD_STATE="${HOLD_STATE:-performance}"
GPU_HOLD_SUDO="${GPU_HOLD_SUDO:-sudo -n}"
GPU_LOWPOWER_SH="${GPU_LOWPOWER_SH:-/usr/local/sbin/gpu-lowpower.sh}"
_GH_LVL=power_dpm_force_performance_level
_GH_ST=power_dpm_state
_GH_HELD=""

_gh_write() {   # <value> <path>
  if [ -w "$2" ]; then
    printf "%s\n" "$1" > "$2" 2>/dev/null
  else
    printf "%s\n" "$1" | $GPU_HOLD_SUDO tee "$2" >/dev/null 2>&1
  fi
}

_gh_put() {   # <want> <path> -- write only when the read-back differs; 0 when it ends up matching
  local got
  got=$(cat "$2" 2>/dev/null)
  [ "$got" = "$1" ] && return 0
  _gh_write "$1" "$2"
  got=$(cat "$2" 2>/dev/null)
  [ "$got" = "$1" ]
}

gpu_hold_cards() {
  local d
  if [ -n "${GPU_CARDS:-}" ]; then printf "%s\n" $GPU_CARDS; return 0; fi
  for d in "$DRM_ROOT"/card[0-9]*; do
    if [ -e "$d/device/$_GH_LVL" ]; then printf "%s\n" "${d##*/}"; fi
  done
  return 0
}

gpu_hold_acquire() {
  [ "$GPU_HOLD" = 1 ] || return 0
  local c dev got n=0
  for c in $(gpu_hold_cards); do
    dev="$DRM_ROOT/$c/device"
    if [ ! -e "$dev/$_GH_LVL" ]; then log "gpu_hold: $c has no $_GH_LVL"; return 1; fi
    _GH_HELD="$_GH_HELD $c"      # recorded BEFORE writing so release covers a half-applied hold
    if ! _gh_write "$HOLD_LEVEL" "$dev/$_GH_LVL"; then log "gpu_hold: cannot write $c $_GH_LVL"; return 1; fi
    if [ -e "$dev/$_GH_ST" ]; then
      if ! _gh_write "$HOLD_STATE" "$dev/$_GH_ST"; then log "gpu_hold: cannot write $c $_GH_ST"; return 1; fi
      got=$(cat "$dev/$_GH_ST" 2>/dev/null)
      if [ "$got" != "$HOLD_STATE" ]; then log "gpu_hold: $c $_GH_ST=${got:-unreadable} want $HOLD_STATE"; return 1; fi
    fi
    got=$(cat "$dev/$_GH_LVL" 2>/dev/null)
    if [ "$got" != "$HOLD_LEVEL" ]; then log "gpu_hold: $c $_GH_LVL=${got:-unreadable} want $HOLD_LEVEL"; return 1; fi
    n=$((n + 1))
  done
  if [ "$n" -eq 0 ]; then log "gpu_hold: no amdgpu card exposes $_GH_LVL"; return 1; fi
  log "gpu_hold: held${_GH_HELD} at $HOLD_LEVEL/$HOLD_STATE"
  return 0
}

gpu_hold_release() {
  local c dev got rc=0 had="$_GH_HELD"
  for c in $_GH_HELD; do
    dev="$DRM_ROOT/$c/device"
    if ! _gh_put low "$dev/$_GH_LVL"; then
      log "gpu_hold: RESTORE FAILED $c $_GH_LVL=$(cat "$dev/$_GH_LVL" 2>/dev/null) want low"; rc=1
    fi
    if [ -e "$dev/$_GH_ST" ] && ! _gh_put battery "$dev/$_GH_ST"; then
      log "gpu_hold: RESTORE FAILED $c $_GH_ST=$(cat "$dev/$_GH_ST" 2>/dev/null) want battery"; rc=1
    fi
  done
  if [ "$rc" -eq 0 ]; then
    _GH_HELD=""                    # keep the list on failure so the EXIT trap retries
    if [ -n "$had" ] && [ -x "$GPU_LOWPOWER_SH" ]; then
      if ! "$GPU_LOWPOWER_SH" --check >/dev/null 2>&1; then log "gpu_hold: gpu-lowpower.sh --check FAILED after restore"; rc=1; fi
    fi
  fi
  return $rc
}
