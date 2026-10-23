#!/usr/bin/env bash
# wq#4486 — NFS is unused: /nfs/rootfs is the only export (`/etc/exports`:
# `/nfs/rootfs *(rw,sync,no_root_squash,no_subtree_check)`), the directory is
# empty (created 2026-10-21, 4.0K, zero files), and nothing in this repo
# references it (no PXE/netboot/dnsmasq config pointing at it). `rw` +
# `no_root_squash` + a wildcard client list is the worst combination NFS
# supports — any host that can reach :2049 mounts it and writes as root.
#
# This removes the live export now (exportfs -ra), which is NOT a blocked
# verb. It does NOT stop/disable nfs-server or rpcbind — that's
# `systemctl stop/disable/mask`, hard-denied for every worker session by
# harness/guard.sh:151-152 unconditionally (not a Thor-only or fire-phase-only
# rule), logged as ISSUE-LOG row for this ticket. Full service retirement
# (disable-nfs-server.sh in this directory) is queued for Frank or another
# session not under that guard.
set -euo pipefail

exports=/etc/exports
marker="wq4486: export removed"

if grep -q "$marker" "$exports" 2>/dev/null; then
  echo "already unexported, skipping"
else
  cp "$exports" "$exports.wq4486.bak"
  sed -i "s#^/nfs/rootfs .*#\# $marker, was: &#" "$exports"
fi

exportfs -ra
echo "APPLIED. Verify: exportfs -v prints nothing for /nfs/rootfs."
