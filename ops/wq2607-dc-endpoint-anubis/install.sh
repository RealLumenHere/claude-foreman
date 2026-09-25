#!/usr/bin/env bash
# wq#2607 Anubis DC endpoint bundle. The fleet_tools.yaml edit is a live edit outside
# version control (same shape as wq#1720/wq#2095) so this only verifies that part;
# it never installs anything on Anubis — this BUILD worker has no exec grant there
# (kb/ISSUE-LOG.md #740). See README.md for the exact remaining commands.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
CTX=/home/lumen/minerva/context

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for f in fleet_tools.yaml FLEET_TOOLS.md; do
    vcontains "$CTX/$f" "mcp_ha_endpoints"
    vcontains "$CTX/$f" "mcp-anubis"
  done
  vdone
fi

echo "wq2607-dc-endpoint-anubis: nothing to install from this lane; the fleet_tools.yaml" >&2
echo "live edit is recorded in wq2607_fleet_tools.diff, and the Anubis-side units/scripts" >&2
echo "in this directory are staged for manual/elevated deployment — see README.md." >&2
exit 2
