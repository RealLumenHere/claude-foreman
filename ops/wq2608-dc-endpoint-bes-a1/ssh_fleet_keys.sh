#!/usr/bin/env bash
# wq#2608 -- give Bes's `bes` user an ssh identity + trust to the rest of the
# fleet, so a Claude session that lands on Bes (per
# MCP_CONTROL_PLANE_HA_DESIGN.md §3.2/§3.5, mirroring what Anubis needs in
# wq#2607) can run the standard SSH-wrapped Lumen commands unchanged.
#
# This is a two-sided bootstrap and genuinely can't be done from ONE session
# with ssh access to only one side:
#   - generating the keypair needs access to Bes (bes user's $HOME)
#   - trusting it on every other node needs write access to THEIR
#     authorized_keys, which nothing in this ticket's capability grant reaches
# So this script does side 1 (run ON Bes) and prints the exact side-2 command
# per target node for whoever has -- or is granted -- fleet-wide reach
# (fleet-exec's `copy` verb already allows landing files in each node's
# lumen-drop; appending to authorized_keys itself is not a fleet-exec verb
# today and would need doing by hand or a follow-up ticket).
set -euo pipefail

BES_HOME=/home/bes
KEY="$BES_HOME/.ssh/id_ed25519"

mkdir -p "$BES_HOME/.ssh"
chmod 700 "$BES_HOME/.ssh"

if [ -f "$KEY" ]; then
  echo "key already exists at $KEY -- not regenerating (would break existing trust)"
else
  ssh-keygen -t ed25519 -N "" -C "bes-mcp-endpoint-wq2608" -f "$KEY"
  chown -R bes:bes "$BES_HOME/.ssh"
  echo "generated $KEY"
fi

PUBKEY=$(cat "${KEY}.pub")
echo
echo "Bes pubkey:"
echo "$PUBKEY"
echo
echo "Append the line above to ~/.ssh/authorized_keys on EACH of these (fleet-exec-"
echo "policy.yaml's ssh: allowlist -- harness/fleet-exec-policy.yaml:174):"
echo "  lumen (100.117.66.102), isis, anubis, maat, sobek, hathor"
echo "(thor is explicitly out of scope -- instinct tier 0 #1, never.nodes in the policy)"
echo
echo "Then from Bes, BatchMode-test each (per design §3.2's requirement):"
cat <<'EOF'
for h in lumen@100.117.66.102 isis anubis maat sobek hathor; do
  ssh -o BatchMode=yes -o ConnectTimeout=5 "$h" true \
    && echo "OK  $h" || echo "FAIL $h"
done
EOF
echo "(the loop above is illustrative for a human/root session -- this worker's own"
echo "sandbox structurally refuses shell for-loops, per its own operating rules)"
