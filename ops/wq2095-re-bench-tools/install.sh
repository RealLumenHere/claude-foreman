#!/usr/bin/env bash
# wq#2095 RE bench inventory in fleet_tools. The live edit is to minerva/context/
# (outside version control), so this bundle has nothing to install: it only verifies.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
CTX=/home/lumen/minerva/context

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for f in fleet_tools.yaml FLEET_TOOLS.md; do
    vcontains "$CTX/$f" "re_bench_hardware"
    vcontains "$CTX/$f" "pi-pico-serprog"
    vcontains "$CTX/$f" "ch341a-programmer"
    vcontains "$CTX/$f" "soic-clips"
    vcontains "$CTX/$f" "NO PURCHASE NEEDED"
  done
  vcontains "$CTX/claude_context.sh" "FLEET_TOOLS"
  vdone
fi

echo "wq2095-re-bench-tools: nothing to install; the live edit is recorded in wq2095_fleet_tools.diff (see README.md)" >&2
exit 2
