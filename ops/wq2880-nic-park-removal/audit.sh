#!/usr/bin/env bash
# wq#2880 fleet audit: is any nic-park leftover still installed? Read-only, no root.
# Usage: audit.sh [node ...]   (default: every node with a tailscale_ip fact)
# Nodes are ssh aliases, same as fleet-exec uses. Per node, prints one line:
#   <node> unit=<is-enabled> script=<present|absent> unbound_nics=<pci addrs>
# unbound_nics lists network-class (0x02xxxx) PCI devices with no driver, which
# is what a parked port looks like. UNREACHABLE means ssh failed, not clean.
set -uo pipefail

nodes=("$@")
if [ ${#nodes[@]} -eq 0 ]; then
  mapfile -t nodes < <(python3 /home/lumen/minerva/lumen_state.py current "" tailscale_ip \
    | sed -nE 's/^\s*(\S+)\.tailscale_ip\s*=.*/\1/p')
fi

probe='u=$(systemctl is-enabled nic-park.service 2>/dev/null | head -1)
[ -e /usr/local/sbin/nic-park ] && s=present || s=absent
un=""
for d in /sys/bus/pci/devices/*; do
  case "$(cat "$d/class" 2>/dev/null)" in 0x02*) [ -e "$d/driver" ] || un="$un${un:+,}${d##*/}";; esac
done
echo "unit=${u:-unknown} script=$s unbound_nics=${un:-none}"'

for n in "${nodes[@]}"; do
  out=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$n" "$probe" 2>/dev/null) \
    && echo "$n $out" || echo "$n UNREACHABLE"
done
