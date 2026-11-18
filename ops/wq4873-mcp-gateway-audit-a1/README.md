# wq#4873 mcp-gateway security audit + hardening

Security audit of `/home/lumen/agent/mcp_gateway.py` (port 3333).
Builds on wq#2748 (fail-closed auth).

## Findings addressed

| # | Finding | Severity | Fix |
|---|---------|----------|-----|
| 1 | `run_shell` executes arbitrary commands as lumen with no filtering | CRITICAL | **Removed entirely** — no shell exec tool |
| 2 | No per-tool authorization — read-only and admin tools share one token | HIGH | 3-tier auth: read/write/admin. Admin requires separate `MCP_GATEWAY_ADMIN_TOKEN` |
| 3 | No audit trail for tool arguments | HIGH | Structured JSONL audit log at `logs/mcp_gateway_audit.jsonl` with argument recording + redaction |
| 4 | CORS `allow_origins=["*"]` | MEDIUM | Restricted to localhost + tailnet CGNAT range (100.x.x.x) via regex |
| 5 | Stale Ghost CMS / Groq / LibreChat references | LOW | Removed: Ghost port check, `get_token_budget` tool (Groq refs), LibreChat probe |
| 6 | `datetime.utcnow()` deprecated | LOW | Replaced with `datetime.now(timezone.utc)` throughout |

## Tools (7, down from 9)

| Tool | Level | Notes |
|------|-------|-------|
| get_queue_status | read | |
| get_agent_logs | read | Lines capped at 200 |
| get_memory | read | |
| get_agent_status | read | Ghost probe removed |
| add_task | write | |
| clear_failed_tasks | write | |
| restart_agent | admin | Requires admin token; disabled when token unset |

**Removed:** `run_shell` (security), `get_token_budget` (stale Groq refs)

## Deploy

1. Back up: `cp /home/lumen/agent/mcp_gateway.py /home/lumen/agent/mcp_gateway.py.bak.$(date +%Y%m%d_%H%M%S)`
2. Generate admin token: `python3 -c "import secrets; print(secrets.token_urlsafe(48))" > /tmp/admin_token`
3. Add to secrets: append `MCP_GATEWAY_ADMIN_TOKEN=<token>` to `/home/lumen/.secrets/mcp-gateway.env`
4. Copy: `cp ops/wq4873-mcp-gateway-audit-a1/mcp_gateway.py /home/lumen/agent/mcp_gateway.py`
5. Restart: `systemctl restart mcp-gateway`
6. Verify: `curl -s http://localhost:3333/health | jq .version` → `"2.0"`

## Retirement question

This gateway is NOT the fleet MCP gateway. Claude Code agents use `.claude.json` MCP
servers (council, docker, fleet-memory, fleet-state, gitea, grafana, playwright,
stealth-browser) which bypass `mcp_gateway.py` entirely. The only remaining consumers
are mobile/Android Claude sessions that connect over Tailscale. If those sessions are
migrated to Claude Code MCP servers, this gateway can be retired. Decision is Frank's.

## Tests

31 tests covering all 6 findings + tool/handler/level consistency.

```
python3 -m pytest ops/wq4873-mcp-gateway-audit-a1/test_hardened_gateway.py -v
```
