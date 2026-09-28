#!/usr/bin/env bash
# wq#2844 — production netdata agent for the MacPro6,1 nodes (hathor, nephthys, khepri).
#
# WHY: ops/fan_floor_alarm.py (wq#2510) reads sensors.* charts from netdata on
# <node tailscale ip>:19999. Only isis had netdata. On hathor, nephthys and khepri the
# alarm answered "netdata did not answer", so nephthys — the node that dropped three
# times in four days — had no fan or thermal watcher at all.
#
# NOT ops/wq394-netdata-canary/install.sh. That one is the lumen canary: it pipes
# get.netdata.cloud/kickstart.sh into root and also deploys the 48h measurement sampler.
# This bundle uses the official Netdata apt repo (public key pinned by sha256 below), the
# same 2.11.x package set isis runs, and the wq#394 config template (tailnet-bind,
# agent-only, no cloud claim).
#
#   install.sh --verify         read-only, from Lumen (wq#1696 contract): each node has
#                               netdata enabled+active, bound to its tailnet IP ONLY, and
#                               fan_floor_alarm can resolve its Te0T/fan1 charts on it.
#   install.sh <tailscale-ip>   install ON the node, as root. Idempotent: a second run
#                               changes nothing. Never upgrades an installed netdata (that
#                               is the fleet-update flow) and never reboots.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
NODES="${NETDATA_MAC_NODES:-hathor nephthys khepri}"
KEY_SHA256=9cca67266d4aae9dc34662bf8a2e207e82ee4a59b76273bb67bab553c6da86b1
KEY_DST=/usr/share/keyrings/netdata-archive-keyring.gpg
SOURCES_DST=/etc/apt/sources.list.d/netdata.sources
CONF_DST=/etc/netdata/netdata.conf

_render() { sed "s/TAILSCALE_IP/$1/g" "$SRC/netdata.conf.template"; }
_sha() { sha256sum "$1" | cut -d' ' -f1; }

# ---- verify (from Lumen, read-only) ----
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  [ "$(_sha "$SRC/netdata-archive-keyring.gpg")" = "$KEY_SHA256" ] || { echo "ERR bundled keyring does not match the pinned sha256"; exit 2; }
  roster="$(fleet-inv ssh-list --all 2>/dev/null)" || { echo "ERR fleet-inv ssh-list failed"; exit 2; }
  for n in $NODES; do
    ip="$(printf '%s\n' "$roster" | awk -v n="$n" '$1==n {split($2,a,"@"); print a[2]}')"
    [ -n "$ip" ] || { echo "ERR no tailscale ip for $n in fleet-inv"; _V_ERR=1; continue; }
    vremote_up "$n" || continue
    vremote_unit "$n" netdata
    vremote_file "$n" "$SRC/netdata-archive-keyring.gpg" "$KEY_DST"
    vremote_file "$n" "$SRC/netdata.sources" "$SOURCES_DST"
    out="$($_VSSH "$n" 'netdata -v 2>/dev/null | head -1; ss -ltnH "sport = :19999" 2>/dev/null | awk "{print \$4}" | sort | paste -sd,' 2>/dev/null || true)"
    ver="$(printf '%s\n' "$out" | sed -n 1p)"
    listen="$(printf '%s\n' "$out" | sed -n 2p)"
    case "$ver" in "netdata v2.11."*) ;; *) _vmiss "$n: netdata version '${ver:-none}' (want v2.11.x)";; esac
    [ "$listen" = "$ip:19999" ] || _vmiss "$n: :19999 listeners '${listen:-none}' (want exactly $ip:19999, tailnet only)"
    python3 - "$SRC/.." "$ip:19999" <<'PY' >/dev/null 2>&1 || _vmiss "$n: fan_floor_alarm cannot resolve its Te0T/fan1 charts on $ip:19999"
import sys
sys.path.insert(0, sys.argv[1])
import fan_floor_alarm as f
f.resolve_charts(sys.argv[2])
PY
  done
  vdone
fi

# ---- install (on the node, as root) ----
[ "$(id -u)" = 0 ] || { echo "install: run as root on the target node (see RUNBOOK.md)" >&2; exit 2; }
IP="${1:?usage: install.sh <tailscale-ip-of-this-host> | --verify}"
case "$IP" in 100.*.*.*) ;; *) echo "install: '$IP' is not a tailnet (100.x) address" >&2; exit 2;; esac
ip -4 -o addr show | grep -q " $IP/" || { echo "install: $IP is not configured on this host; refusing to bind netdata to it" >&2; exit 2; }
command -v apt-get >/dev/null || { echo "install: apt-get not found" >&2; exit 2; }
if command -v needrestart >/dev/null && ! grep -rqsE "restart\}[[:space:]]*=[[:space:]]*'l'" /etc/needrestart/conf.d/; then
  echo "install: needrestart is not list-only here; an apt install could restart serving units. Install the zz-needrestart-list-only.conf first (wq#855)." >&2
  exit 2
fi
[ "$(_sha "$SRC/netdata-archive-keyring.gpg")" = "$KEY_SHA256" ] || { echo "install: bundled keyring does not match the pinned sha256" >&2; exit 2; }

cmp -s "$SRC/netdata-archive-keyring.gpg" "$KEY_DST" 2>/dev/null || install -m0644 "$SRC/netdata-archive-keyring.gpg" "$KEY_DST"
cmp -s "$SRC/netdata.sources" "$SOURCES_DST" 2>/dev/null || install -m0644 "$SRC/netdata.sources" "$SOURCES_DST"

if ! dpkg -s netdata >/dev/null 2>&1; then
  apt-get update -qq
  sim="$(apt-get -s install netdata 2>&1)"
  if printf '%s\n' "$sim" | grep -q '^Remv'; then
    echo "install: apt simulation would REMOVE packages; refusing" >&2; printf '%s\n' "$sim" | grep '^Remv' >&2; exit 3
  fi
  up="$(printf '%s\n' "$sim" | sed -n 's/^\([0-9][0-9]*\) upgraded.*/\1/p' | head -1)"
  [ "${up:-x}" = 0 ] || { echo "install: apt simulation would UPGRADE ${up:-?} packages; refusing" >&2; exit 3; }
  DEBIAN_FRONTEND=noninteractive apt-get install -y netdata
fi

# netdata.conf is a packaged conffile: keep the original once, then apply the template.
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
_render "$IP" > "$tmp"
changed=0
if ! cmp -s "$tmp" "$CONF_DST" 2>/dev/null; then
  [ -e "$CONF_DST.orig-wq2844" ] || cp -a "$CONF_DST" "$CONF_DST.orig-wq2844" 2>/dev/null || true
  install -o root -g netdata -m0640 "$tmp" "$CONF_DST"
  changed=1
fi
systemctl enable netdata >/dev/null 2>&1
if [ "$changed" = 1 ] || ! systemctl is-active --quiet netdata; then systemctl restart netdata; fi

for _ in $(seq 1 30); do
  ss -ltnH "sport = :19999" | awk '{print $4}' | grep -qx "$IP:19999" && break
  sleep 1
done
listen="$(ss -ltnH "sport = :19999" | awk '{print $4}' | sort | paste -sd,)"
[ "$listen" = "$IP:19999" ] || { echo "install: netdata listeners are '$listen', want exactly $IP:19999" >&2; exit 1; }
echo "install: OK — $(netdata -v | head -1) bound to $IP:19999 only"
