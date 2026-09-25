#!/usr/bin/env bash
# wq#2613 -- ingest the mcp_endpoints roster into minerva state, same shape as
# wq#2607/wq#2608's per-node `lumen_state.py ingest <node>.mcp_endpoint <url>`
# calls but as ONE subject/predicate holding the whole ordered roster, so a
# reader (claude_context.sh --core, GET /pool cross-check) gets the pool shape
# in a single lookup instead of four separate facts it has to know to join.
#
# Same capability gap wq#2608's lumen_state_ingest.sh hit and logged: this
# BUILD lane's Bash allowlist (harness/worker-settings.json) does not include
# `/home/lumen/bin/lumen_state.py` or `/home/lumen/bin/state-regen` as an
# allowed prefix (confirmed live this ticket: "This command requires
# approval", not a guard.sh deny -- a capability gap, not a policy refusal),
# and /home/lumen/minerva is deliberately outside this worktree's --add-dir
# list (wq#1467, credentials mirrored into context/). Run this from a session
# with that access:
set -euo pipefail

ROSTER='[
  {"node": "lumen",    "pref": 1, "url": "https://mcp-backend.lumennode.uk",  "status": "live"},
  {"node": "anubis",   "pref": 2, "url": "https://mcp-anubis.lumennode.uk",   "status": "prepared_not_deployed"},
  {"node": "bes",      "pref": 3, "url": "https://mcp-bes.lumennode.uk",      "status": "prepared_not_deployed"},
  {"node": "nephthys", "pref": 4, "url": "https://mcp-failover.lumennode.uk", "status": "units_disabled_since_2026-08-23"}
]'

cat <<EOF
/home/lumen/bin/lumen_state.py ingest mcp_pool mcp_endpoints '$ROSTER' --source wq2613
/home/lumen/bin/state-regen
EOF

echo
echo "Then apply ops/wq2613-dc-endpoint-parity-check-a1/wq2613_fleet_tools.diff's"
echo "commander_mcp entry to /home/lumen/minerva/context/fleet_tools.yaml by hand"
echo "(outside version control, same precedent as wq#1720/wq#2095/wq#2607), bump"
echo "its updated: date, and re-render with /home/lumen/bin/fleet-tools-render."
echo
echo "Update each node's 'status' above once wq#2614 (CF token) lands and the"
echo "prepared endpoints go live, and once the un-retire checklist item in"
echo "docs/fleet/UN_RETIRE_CHECKLIST.md is run for nephthys -- this roster is a"
echo "point-in-time snapshot, not a live query, until the nightly parity check"
echo "(dc-endpoint-parity-check.py, this directory) is wired to re-ingest it."
