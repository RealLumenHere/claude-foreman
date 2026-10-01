#!/usr/bin/env bash
# ops/wq3319-a1/install.sh — wq#3319 [physical] wq#3294-C2 staged artifacts.
#
# This bundle ships no deploy step of its own: the Station P1 Pro purchase is
# a physical task Frank has not completed yet, and the on-arrival apply
# (clone ops/wq2608-dc-endpoint-bes-a1, join the mcpw pool per README.md) is a
# follow-up ticket once the hardware exists and is reachable over ssh.
#
# --verify instead checks that the staged reference artifacts haven't drifted
# from the live sources this ticket verified them against, per
# ops/VERIFY_CONTRACT.md's "a bundle that installs nothing at all still needs
# a handler" rule: a silent schema change elsewhere would otherwise leave the
# on-arrival runbook's example wrong with nothing to catch it.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  # commander-pool.example.json still only uses keys pool_failover.py reads,
  # and still has at least one well-formed backend entry.
  python3 "$SRC/_verify_schema.py" "$SRC/commander-pool.example.json" \
    || _vmiss "commander-pool.example.json schema check failed (see stdout above)"

  # README's claim that commander-pool-failover.service reads
  # /home/bes/etc/commander-pool.json is still true of the live unit source.
  vcontains "$SRC/../wq3294-commander-ha/commander-pool-failover.service" \
    'config /home/bes/etc/commander-pool\.json'

  # README's on-arrival memory-limit reference point (desktop-commander-mcp's
  # 20G/26G, cited as the fallback for a dedicated node with headroom) still
  # matches the live unit.
  _dc_unit="$(systemctl cat desktop-commander-mcp.service 2>/dev/null || true)"
  case "$_dc_unit" in
    *MemoryHigh=20G*) ;;
    *) _vmiss "desktop-commander-mcp.service no longer shows MemoryHigh=20G (README's on-arrival reference point)" ;;
  esac
  case "$_dc_unit" in
    *MemoryMax=26G*) ;;
    *) _vmiss "desktop-commander-mcp.service no longer shows MemoryMax=26G (README's on-arrival reference point)" ;;
  esac

  vdone
fi

echo "ops/wq3319-a1 has no install step: the P1 Pro purchase is a physical task (not yet done)," >&2
echo "and the on-arrival apply (clone ops/wq2608-dc-endpoint-bes-a1, join the mcpw pool) is a follow-up ticket." >&2
exit 2
