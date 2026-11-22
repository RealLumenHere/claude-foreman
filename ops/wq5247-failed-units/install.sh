#!/usr/bin/env bash
# wq#5247 — three failed units left over from the wq#4999 aq/LRA sweep (wq#5245 split):
#   lumen    audit-rules.service           augenrules --load exit 1 since 2026-11-12 23:35Z
#   maksoor  max-brightness.service        laptop-panel unit after the 2026-11-16 board swap
#   bes      grub-initrd-fallback.service  grub-editenv on /boot/grub/grubenv
# README.md has the evidence and the fix class each script acts on.
#
#   bash install.sh --verify            read-only, any user: exit 0 when none of the three is failed
#   bash install.sh --diagnose [NODE]   print root-cause evidence, change nothing
#   bash install.sh [NODE]              diagnose, then apply only the fix the evidence matches
# NODE = lumen | maksoor | bes (default: all). The Lumen part needs root: run as root, or
# as lumen with sudo rights (it re-execs under sudo). maksoor/bes are reached with the
# lumen user's ssh key and escalate there with `sudo -n`.
set -uo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# ssh targets — lumen_state.py current maksoor (tailscale_ip; role: user maksoor) and
# bes (bes.ssh = bes@100.110.156.91, key-only).
MAKSOOR=maksoor@100.81.204.74
BES=bes@100.110.156.91
LUMEN_KEY=/home/lumen/.ssh/id_ed25519

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only: three is-failed queries.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  st="$(systemctl is-failed audit-rules.service 2>/dev/null || true)"
  [ "$st" = failed ] && _vmiss "lumen: audit-rules.service failed"
  if vremote_up "$MAKSOOR"; then
    st="$($_VSSH "$MAKSOOR" "systemctl is-failed max-brightness.service" 2>/dev/null || true)"
    [ "$st" = failed ] && _vmiss "maksoor: max-brightness.service failed"
  fi
  if vremote_up "$BES"; then
    st="$($_VSSH "$BES" "systemctl is-failed grub-initrd-fallback.service" 2>/dev/null || true)"
    [ "$st" = failed ] && _vmiss "bes: grub-initrd-fallback.service failed"
  fi
  vdone
fi

ARGS=("$@")
DRY=0
if [ "${1:-}" = --diagnose ]; then DRY=1; shift; fi
ONLY="${1:-all}"
case "$ONLY" in
  all|lumen|maksoor|bes) ;;
  *) echo "usage: $0 [--verify | --diagnose] [lumen|maksoor|bes]" >&2; exit 2 ;;
esac
if [ "$(id -u)" -ne 0 ] && { [ "$ONLY" = all ] || [ "$ONLY" = lumen ]; }; then
  exec sudo -- bash "$0" "${ARGS[@]}"
fi

TS="$(date -u +%Y%m%d_%H%M%S)"
RC=0
rssh() {  # rssh HOST ARGS... — ssh with the lumen key, also when this script runs as root
  local h="$1"; shift
  local cmd=(ssh -i "$LUMEN_KEY" -o BatchMode=yes -o ConnectTimeout=8 "$h" "$@")
  if [ "$(id -u)" -eq 0 ]; then runuser -u lumen -- "${cmd[@]}"; else "${cmd[@]}"; fi
}
run_remote() {  # run_remote HOST BODY — lib.sh travels with the body over stdin
  if ! cat "$SRC/lib.sh" "$SRC/$2" | rssh "$1" bash -s -- "$DRY" "$TS"; then
    echo "UNFIXED $1 (output above)"; RC=1
  fi
}

case "$ONLY" in all|lumen)
  bash "$SRC/lumen-audit.sh" "$DRY" "$TS" || RC=1 ;;
esac
case "$ONLY" in all|maksoor) run_remote "$MAKSOOR" remote-maksoor.sh ;; esac
case "$ONLY" in all|bes)     run_remote "$BES" remote-bes.sh ;; esac

[ "$DRY" -eq 1 ] || echo "next: bash $SRC/install.sh --verify"
exit "$RC"
