#!/bin/bash
# Run ON LUMEN after serial-mcp is active on Maksoor. Local scope for /home/lumen/re only (same
# precedent as ghidra-lumen): these are UART write/control tools with no auth, so they are NOT
# registered user-wide. To widen: add -s user (every Claude session on Lumen, fworkers included).
set -euo pipefail
cd /home/lumen/re
cp -p /home/lumen/.claude.json "/home/lumen/.claude.json.bak.$(date +%Y%m%d_%H%M%S)"
/home/lumen/.npm-global/bin/claude mcp add --transport http serial-maksoor http://100.81.204.74:8400/mcp
timeout 60 /home/lumen/.npm-global/bin/claude mcp list 2>&1 | grep serial-maksoor
