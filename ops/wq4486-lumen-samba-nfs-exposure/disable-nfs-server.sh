#!/usr/bin/env bash
# wq#4486 — retire the idle NFS server stack (nfs-server, rpcbind) on Lumen.
#
# NOT run by the wq#4486 worker: harness/guard.sh:151-152 hard-denies any
# `systemctl stop|disable|mask` call from every worker session, unconditionally
# (not gated to fire-mode or to Thor) — ISSUE-LOG row for this ticket records
# the block. The live data-exposure risk (the wildcard rw no_root_squash
# export) is already closed by apply-nfs-unexport.sh; this script is the
# remainder — fully closing ports 2049/111/20048 — queued for Frank or a
# session not under that guard to run on Lumen:
#
#   sudo bash disable-nfs-server.sh
#
# Rollback if something turns out to depend on it:
#   sudo systemctl enable --now rpcbind nfs-server
#   sudo bash -c 'echo "/nfs/rootfs *(rw,sync,no_root_squash,no_subtree_check)" >> /etc/exports && exportfs -ra'
set -euo pipefail

systemctl disable --now nfs-server
systemctl disable --now rpcbind

echo "DONE. Verify: ss -tln | grep -E ':2049|:111|:20048' prints nothing."
