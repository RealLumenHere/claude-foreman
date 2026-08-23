#!/usr/bin/env bash
# wq#857 — fan out install-journal-shipper.sh to every fleet node. NOT run by
# the fworker BUILD-lane agent that staged this (see README.md): needs root on
# 13-15 remote targets, which guard.sh unconditionally denies for this harness.
# Run from Frank's interactive session or a claude-web session that has
# ~/.ssh/fleet_config.
#
# Host list is discovered from ~/.ssh/fleet_config at run time rather than
# hardcoded here — this session couldn't reliably enumerate it (grep/wc against
# files outside the worktree got sandbox-blocked partway through recon), and a
# guessed, possibly-stale list would silently under-cover the fleet. Confirm
# the resolved list against FLEET_MAP.md before proceeding.
#
# Usage: rollout-fleet-shipper.sh <tailscale-ip-of-bes> [extra excluded host]...
set -euo pipefail

BES_ADDR="${1:?usage: rollout-fleet-shipper.sh <tailscale-ip-of-bes> [exclude...]}"
shift
EXCLUDE=("bes" "$@")  # Bes ships its own journal too, but via localhost, not a hop through itself over the tailnet — handled separately below.

mapfile -t HOSTS < <(awk '/^Host / {print $2}' ~/.ssh/fleet_config | grep -Ev -- '-lan$|-ts$')

FILTERED=()
for h in "${HOSTS[@]}"; do
  skip=0
  for e in "${EXCLUDE[@]}"; do [[ "$h" == "$e" ]] && skip=1; done
  [[ $skip -eq 0 ]] && FILTERED+=("$h")
done

echo "Resolved ${#FILTERED[@]} target hosts: ${FILTERED[*]}"
echo "Cross-check this against FLEET_MAP.md before continuing (decommissioned nodes"
echo "like nephthys should not appear; anything unreachable should be triaged, not"
echo "silently skipped)."
read -r -p "Proceed with rollout to all of the above? [y/N] " confirm
[[ "$confirm" == "y" || "$confirm" == "Y" ]] || { echo "Aborted."; exit 1; }

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
FAILED=()
for h in "${FILTERED[@]}"; do
  echo "== $h =="
  if rsync -a "$SELF_DIR/install-journal-shipper.sh" "$SELF_DIR/journal-upload.conf.template" "${h}:/tmp/wq857-shipper/" 2>&1 \
     && ssh -o BatchMode=yes -o ConnectTimeout=8 "$h" "sudo bash /tmp/wq857-shipper/install-journal-shipper.sh ${BES_ADDR}"; then
    echo "-- $h OK --"
  else
    echo "-- $h FAILED --"
    FAILED+=("$h")
  fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "Rollout incomplete. Failed hosts: ${FAILED[*]}"
  exit 1
fi
echo "Rollout complete: ${#FILTERED[@]} nodes shipping journald to Bes."
