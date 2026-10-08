#!/usr/bin/env bash
# wq#3653 — OPNsense + Tailscale + Pi-hole + exit-node runbook.
#
# This bundle has no automated install step: none of the three target boxes (Topton
# OPNsense, Pi-hole RPi, Flint 3) is reachable from a worker sandbox or listed in
# harness/fleet-exec-policy.yaml's ssh allowlist (see README.md). RUNBOOK.md is the
# step list an operator with LAN/console access runs by hand, using
# opnsense_tailscale_cli.sh and pihole_fleet_bypass.py for the automatable steps.
#
# wq#1696 deploy-verify contract (ops/_verify.sh): every install.sh answers --verify,
# even one that installs nothing itself, so ops-deploy-drift can track this bundle's
# live state instead of reading it as ABSENT forever. Checks only the two RUNBOOK
# items that have a real, author-assigned remote identifier to anchor on
# (OPNsense's tailscale hostname, set by RUNBOOK.md item 2's `tailscale up
# --hostname=opnsense-topton`) — Pi-hole and the Flint 3 were never assigned a
# hostname by this ticket, so there is nothing non-fabricated to ssh to for them yet;
# add their checks here once a later ticket names them. Read-only: every check below
# is `tailscale status`/`pkg info`, never a write.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

OPNSENSE=opnsense-topton

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if vremote_up "$OPNSENSE"; then
    # RUNBOOK.md item 1 / TEST_PLAN.md item 1: plugin installed.
    if ! $_VSSH "$OPNSENSE" "pkg info os-tailscale" >/dev/null 2>&1; then
      _vmiss "$OPNSENSE: os-tailscale not installed (RUNBOOK.md item 1)"
    fi
    # RUNBOOK.md item 2 / TEST_PLAN.md item 2: authenticated, not logged out.
    status="$($_VSSH "$OPNSENSE" "tailscale status" 2>/dev/null || true)"
    case "$status" in
      *"$OPNSENSE"*) ;;
      *) _vmiss "$OPNSENSE: own hostname not in tailscale status (RUNBOOK.md item 2)" ;;
    esac
    case "$status" in
      *"ogged out"*) _vmiss "$OPNSENSE: tailscale status reports Logged out (RUNBOOK.md item 2)" ;;
      *) ;;
    esac
    # RUNBOOK.md item 8 / TEST_PLAN.md item 8d: loop-prevention invariant
    # (NETWORK_PREMORTEM.md §0.4) — accept-dns must stay false regardless of what the
    # tailnet DNS tab says, or OPNsense's Unbound and Pi-hole can deadlock each other.
    dns="$($_VSSH "$OPNSENSE" "tailscale status --json" 2>/dev/null | grep -io '\"accept-\?dns\"[^,}]*' || true)"
    case "$dns" in
      *lse*) ;;  # false/False — accept-dns off
      "") _vmiss "$OPNSENSE: could not read accept-dns from tailscale status --json (RUNBOOK.md item 8)" ;;
      *) _vmiss "$OPNSENSE: accept-dns is not false (RUNBOOK.md item 8 loop-prevention, NETWORK_PREMORTEM.md §0.4): $dns" ;;
    esac
  fi
  vdone
fi

echo "refuse: wq#3653 ships a runbook, not an installer. See RUNBOOK.md and run" >&2
echo "        opnsense_tailscale_cli.sh / pihole_fleet_bypass.py by hand from a" >&2
echo "        session that reaches the Topton, Pi-hole RPi and Flint 3." >&2
exit 1
