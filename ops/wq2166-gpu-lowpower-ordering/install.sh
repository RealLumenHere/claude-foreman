#!/usr/bin/env bash
# wq#2166 — gpu-lowpower ordering fix for the dual-D300 Mac Pro nodes (hathor, nephthys).
#
#   install.sh --verify   read-only, from Lumen: are the nodes actually at dpm=low, and
#                         do the installed files byte-match this bundle?
#   install.sh            install ON the target node, as root. Copies the script, both
#                         units and the udev rule, reloads systemd and udev, enables and
#                         runs the reconciler, then re-reads every card. Exits non-zero
#                         if any card is not at low when it finishes.
#
# The install path has to run on the node because it writes /etc and calls systemctl.
# fleet-exec has no verb that can do that (`run` is read-only, `sudo` is v2) — see
# RUNBOOK.md for the two-command apply.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
NODES="${GPU_LOWPOWER_NODES:-hathor nephthys}"
EXPECTED_CARDS="${EXPECTED_CARDS:-2}"

SH_DST=/usr/local/sbin/gpu-lowpower.sh
UNIT_DST=/etc/systemd/system/gpu-lowpower.service
TMPL_DST=/etc/systemd/system/gpu-lowpower@.service
RULE_DST=/etc/udev/rules.d/99-gpu-lowpower.rules

# One round-trip per node. Every command is read-only: four cats, two systemctl
# queries, four sha256sums.
_probe='
for f in /sys/class/drm/card[0-9]*/device/power_dpm_force_performance_level; do
  [ -e "$f" ] || continue
  c=${f#/sys/class/drm/}; c=${c%%/*}
  printf "level=%s:%s\n" "$c" "$(cat "$f" 2>/dev/null)"
  s=/sys/class/drm/$c/device/power_dpm_state
  [ -e "$s" ] && printf "state=%s:%s\n" "$c" "$(cat "$s" 2>/dev/null)"
done
a=$(systemctl is-active gpu-lowpower.service 2>/dev/null); [ -n "$a" ] || a=absent
printf "active=%s\n" "$a"
printf "status=%s\n" "$(systemctl show gpu-lowpower.service -p ExecMainStatus --value 2>/dev/null)"
for p in /usr/local/sbin/gpu-lowpower.sh /etc/systemd/system/gpu-lowpower.service /etc/systemd/system/gpu-lowpower@.service /etc/udev/rules.d/99-gpu-lowpower.rules; do
  printf "sha=%s:%s\n" "$p" "$(sha256sum "$p" 2>/dev/null | cut -d" " -f1)"
done
'

_sha() { sha256sum "$1" | cut -d' ' -f1; }

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for n in $NODES; do
    out="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$n" "$_probe" 2>/dev/null)" || {
      echo "ERR cannot probe $n"; _V_ERR=1; continue
    }

    # Outcome first: the knob is the thing that matters, the files are how it got there.
    seen=0
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      seen=$((seen + 1))
      case "${line#*:}" in low) ;; *) _vmiss "$n: ${line%%:*} dpm=${line#*:} (want low)";; esac
    done <<EOF_LVL
$(printf '%s\n' "$out" | sed -n 's/^level=//p')
EOF_LVL
    [ "$seen" -ge "$EXPECTED_CARDS" ] || _vmiss "$n: $seen cards expose the dpm knob (want $EXPECTED_CARDS)"

    while IFS= read -r line; do
      [ -n "$line" ] || continue
      case "${line#*:}" in battery) ;; *) _vmiss "$n: ${line%%:*} power_dpm_state=${line#*:} (want battery)";; esac
    done <<EOF_ST
$(printf '%s\n' "$out" | sed -n 's/^state=//p')
EOF_ST

    # A oneshot that applied nothing used to read `active`. Now it can only read
    # `active` with ExecMainStatus=0, i.e. every card read back low.
    active="$(printf '%s\n' "$out" | sed -n 's/^active=//p')"
    status="$(printf '%s\n' "$out" | sed -n 's/^status=//p')"
    [ "$active" = active ] || _vmiss "$n: gpu-lowpower.service is-active=$active (want active)"
    [ "${status:-none}" = 0 ] || _vmiss "$n: gpu-lowpower.service ExecMainStatus=${status:-none} (want 0)"

    for pair in "$SH_DST=gpu-lowpower.sh" "$UNIT_DST=gpu-lowpower.service" \
                "$TMPL_DST=gpu-lowpower@.service" "$RULE_DST=99-gpu-lowpower.rules"; do
      dst="${pair%%=*}"; src="$SRC/${pair#*=}"
      want="$(_sha "$src")"
      got="$(printf '%s\n' "$out" | sed -n "s|^sha=$dst:||p")"
      [ -n "$got" ] || { _vmiss "$n: absent: $dst"; continue; }
      [ "$got" = "$want" ] || _vmiss "$n: differs: $dst (not the bundle's ${pair#*=})"
    done
  done
  vdone
fi

# ---- install (on the node, as root) ----
[ "$(id -u)" = 0 ] || { echo "install: run as root on the target node (see RUNBOOK.md)" >&2; exit 2; }
command -v systemctl >/dev/null || { echo "install: no systemctl on this host" >&2; exit 2; }

have=0
for f in /sys/class/drm/card[0-9]*/device/power_dpm_force_performance_level; do
  if [ -e "$f" ]; then have=$((have + 1)); fi
done
if [ "$have" -lt "$EXPECTED_CARDS" ] && [ "${FORCE:-0}" != 1 ]; then
  echo "install: $have/$EXPECTED_CARDS amdgpu cards expose the dpm knob — refusing." >&2
  echo "install: set FORCE=1 to install anyway (the reconciler will fail until they appear)." >&2
  exit 2
fi

install -m0755 "$SRC/gpu-lowpower.sh"        "$SH_DST"
install -m0644 "$SRC/gpu-lowpower.service"   "$UNIT_DST"
install -m0644 "$SRC/gpu-lowpower@.service"  "$TMPL_DST"
install -m0644 "$SRC/99-gpu-lowpower.rules"  "$RULE_DST"

systemctl daemon-reload
udevadm control --reload
udevadm trigger --subsystem-match=drm --action=add
systemctl enable gpu-lowpower.service
systemctl restart gpu-lowpower.service

echo "install: applied, re-reading every card"
"$SH_DST" --check
echo "install: OK — every card reads low"
