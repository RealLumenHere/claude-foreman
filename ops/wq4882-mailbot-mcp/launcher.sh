#!/usr/bin/env bash
# mailbot-mcp behind lumen_mcp_guard (wq#4882). stdio, spawned per client: no listening port.
# Mail bodies are third-party text, so results are masked and wrapped as untrusted. The allowlist
# is the whole surface; a tool added to server.py stays hidden until it is added here too.
# Needs a guard with --untrusted-output (foreman 2283ca1b, 2026-11-13). The copy under _guard
# predates it until ops/wq4514-mcp-install-batch/install.sh runs again, and then exits on an
# unknown flag: fail closed, never unwrapped. MAILBOT_MCP_GUARD points at a newer copy meanwhile.
set -euo pipefail
GDIR=/home/lumen/.local/share/mcp/_guard
GUARD="${MAILBOT_MCP_GUARD:-$GDIR/lumen_mcp_guard.py}"
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$GDIR/venv/bin/python" -I "$GUARD" --name mailbot \
  --allow mail_status,mail_search,mail_thread,mail_contact,mail_needs_frank,mail_pending_tasks,mail_draft_create,mail_drafts \
  --mask-output /home/lumen/bin/mask-secrets \
  --max-result-bytes 524288 --untrusted-output \
  -- /usr/bin/python3 "$HERE/server.py"
