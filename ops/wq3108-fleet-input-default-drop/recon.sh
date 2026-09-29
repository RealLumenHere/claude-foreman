#!/usr/bin/env bash
# wq#3108 recon — read-only capture of everything genrules.py needs to design a
# node's INPUT default-drop. Run ON the target node as root:
#   sudo bash recon.sh /tmp/wq3108-recon
# Then fetch the printed bundle dir back to Lumen for review. Changes nothing.
set -euo pipefail

base="${1:-/tmp/wq3108-recon}"
host="$(hostname -s)"
dir="${base}/${host}-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$dir"

hostname -f > "$dir/hostname.txt" 2>&1 || hostname > "$dir/hostname.txt"
ip -br addr > "$dir/ip-addr.txt"
ip -br link > "$dir/ip-link.txt"
ip route > "$dir/ip-route.txt"
ip -6 route > "$dir/ip6-route.txt" || true

# The two facts the whole design hangs on: what listens, and what rules exist today.
ss -ltnp  > "$dir/ss-ltnp.txt"
ss -lunp  > "$dir/ss-lunp.txt"
iptables-save  > "$dir/live.v4"
ip6tables-save > "$dir/live.v6"

# Persistence + firewall stack present on this node (decides how disarm persists).
{
  command -v netfilter-persistent || true
  command -v nft || true
  command -v ufw || true
  command -v firewalld || true
  systemctl is-active tailscaled 2>/dev/null || true
} > "$dir/stack.txt"
tailscale status --peers=false > "$dir/tailscale.txt" 2>&1 || true

# DHCPv6 in use? (decides rule #6 of the standard)
ip -6 addr show scope global > "$dir/ip6-global.txt" || true

echo "recon bundle: $dir"
