#!/usr/bin/env bash
# wq#4631 fleet_tools.yaml fleet-memory entry: wq#4554 renamed fleet_facts/fleet_devices/
# fleet_decisions/agent_episodes to retired_*, so the entry's table list was stale. minerva/
# is a primary tree the worker guard refuses to Write/Edit (wq#1719/wq#900) and
# context/fleet_tools.yaml is gitignored live config, so this bundle installs nothing:
# hand-apply wq4631_fleet_tools.diff per README.md. --verify checks the live text.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
CTX=/home/lumen/minerva/context

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$CTX/fleet_tools.yaml" "live tables: memory_events, memory_sources, recipes; retired_fleet_facts"
  vdone
fi

echo "wq4631-fleet-memory-live-tables: nothing to install from this lane —" >&2
echo "apply $SRC/wq4631_fleet_tools.diff to $CTX/fleet_tools.yaml (see README.md)." >&2
exit 1
