#!/usr/bin/env bash
# wq#2608 -- register the new endpoint in fleet state, mirroring wq#2607's
# ask for Anubis ("lumen_state ingest anubis.mcp_endpoint + state-regen;
# fleet_tools.yaml entry").
#
# `lumen_state.py ingest` writes minerva.db directly (research/
# MINERVA_PIPELINE_MAP.md: "writers | lumen_state.py ingest (CLI, any
# session)") -- it is a data write, not a privileged op, but this BUILD
# worker's Bash allowlist (harness/worker-settings.json) does not include
# `/home/lumen/bin/lumen_state.py` or `/home/lumen/bin/state-regen` as
# allowed command prefixes (confirmed live: invoking either here returns
# "This command requires approval", not a guard.sh deny -- a capability gap,
# not a policy refusal). Run from a session whose settings allow it, or once
# https://mcp-bes.lumennode.uk actually answers (this should run AFTER DNS/
# Access land, not before -- ingesting a URL that 403s/DNS-fails is worse
# than not ingesting it):
set -euo pipefail

cat <<'EOF'
/home/lumen/bin/lumen_state.py ingest bes.mcp_endpoint https://mcp-bes.lumennode.uk
/home/lumen/bin/state-regen
EOF

echo
echo "Also add an entry for mcp-bes to /home/lumen/minerva/context/fleet_tools.yaml"
echo "(outside version control -- .gitignore'd in the minerva repo, edited live, same"
echo "as wq#1720's precedent for that file) and re-render with fleet-tools-render so"
echo "claude_context.sh --core picks it up. /home/lumen/minerva is deliberately absent"
echo "from this worktree's --add-dir list and from fleet-exec-policy.yaml's read_dirs"
echo "(credentials mirrored into context/sections, wq#1467) -- this step needs a"
echo "session with that access, not a capability grant on this narrow BUILD ticket."
