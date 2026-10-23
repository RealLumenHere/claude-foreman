#!/usr/bin/env bash
# wq#4486 — restrict smbd's listening socket to the interfaces its own
# `hosts allow` already permits (lo, LAN ens1, tailnet tailscale0), instead of
# 0.0.0.0. Samba IS actively used here (shares lumen-home/lumen-scratch/
# lumen-research; /var/log/samba/log.192.168.8.153 proves a LAN client has
# connected), so this restricts rather than disables, and keeps the LAN path
# alive instead of narrowing to tailnet-only and breaking that client.
#
# Idempotent: skips the insert if already applied. Run as root.
set -euo pipefail

conf=/etc/samba/smb.conf
marker="wq4486 bind interfaces"

if grep -q "$marker" "$conf"; then
  echo "already applied, skipping insert"
else
  cp "$conf" "$conf.wq4486.bak"
  # tailscale0 is a /32 point-to-point interface. Samba's interface resolver
  # does not bind to it regardless of whether it's named by interface
  # ("tailscale0"), IP/CIDR ("100.117.66.102/32"), or bare IP
  # ("100.117.66.102") — all three were tried empirically and none produced
  # a listening socket on the tailnet address (smbd binds lo+ens1 fine, logs
  # no error, simply omits the tailnet one). This is a known class of issue
  # with Samba + WireGuard-style point-to-point interfaces. `hosts allow`
  # still lists the tailnet range for when/if that gets fixed; until then,
  # tailnet SMB access fails closed (no listening socket), not open.
  sed -i "/option cannot handle dynamic or non-broadcast interfaces correctly\\.$/a\\
\\
# $marker (wq#4486): was 0.0.0.0, restricted to the nets hosts-allow permits\\
   interfaces = lo ens1 100.117.66.102\\
   bind interfaces only = yes" "$conf"
fi

testparm -s "$conf" >/dev/null
# smbd binds its listening sockets at startup (no systemd socket-activation
# unit here), so `reload` re-reads smb.conf but does not rebind — a full
# restart is required for the new `interfaces`/`bind interfaces only` to
# take effect on the actual listening socket.
systemctl restart smbd
echo "APPLIED. Verify: ss -tlnp | grep -E ':139|:445' shows only 127.0.0.1/192.168.8.160/100.117.66.102, not 0.0.0.0."
