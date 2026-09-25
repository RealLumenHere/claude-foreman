#!/usr/bin/env bash
# wq#2609 — read-only verification for the Nephthys mcp-failover re-enable.
# Safe to run from anywhere with network access to the public tunnel; the
# authenticated 200 check additionally needs the existing Access service
# token in CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET (never hardcode it
# here — instinct 4, mask before print).
set -uo pipefail

echo "== unauthenticated (want 403 — Access blocks, origin unreachable to us either way) =="
curl -s -o /dev/null -w 'GET  /mcp    -> %{http_code}\n' https://mcp-failover.lumennode.uk/mcp
curl -s -o /dev/null -w 'GET  /health -> %{http_code}\n' https://mcp-failover.lumennode.uk/health

if [ -n "${CF_ACCESS_CLIENT_ID:-}" ] && [ -n "${CF_ACCESS_CLIENT_SECRET:-}" ]; then
  echo "== authenticated tools/list (want 200 with a \"result\" key) =="
  curl -s -m 10 -w '\nHTTP %{http_code}\n' \
    -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
    https://mcp-failover.lumennode.uk/mcp
else
  echo "== authenticated check skipped: export CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET first =="
fi
