#!/bin/bash
# wq#4513 — registers the Maksoor serial-mcp HTTP server with Claude Code on
# Lumen. Run this ON LUMEN (not Maksoor) after serial-mcp.service is active
# on Maksoor and `curl http://100.81.204.74:8000/` responds.
set -euo pipefail

claude mcp add --transport http serial-maksoor http://100.81.204.74:8000
