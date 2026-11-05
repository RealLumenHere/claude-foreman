# MCP Gateway Audit Report — mcp_gateway.py
_Audited 2026-11-05 by claude-web (audit-gateway session). Refs: wq#4873, wq#4874._

## Executive Summary

mcp_gateway.py (333 lines, port 3333) is a FastAPI HTTP/JSON-RPC bridge exposing 9 tools.
**It is RUNNING but has ZERO live consumers.** Claude Code agents use `.claude.json` stdio
MCP servers (fleet-memory, docker, gitea, grafana, etc.) and bypass this gateway entirely.
No `.claude.json` on the fleet references port 3333. `ss -tnp dst :3333` shows zero
established connections. **Recommendation: RETIRE this service.**

## Audit Checklist

### 1. Authentication — PARTIAL (single bearer token)
- Bearer token via `MCP_GATEWAY_TOKEN` env var (drop-in `10-wq2748-token.conf`).
- Token validated with constant-time `hmac.compare_digest`. Minimum 32 chars enforced.
- Burned token `lumen-gateway-2026` refused. Startup fails closed (exit 78) on missing/burned/short token.
- Fixed in wq#2748: auth was dead code (`if False`) and never attached to routes — now enforced.
- Token auth is sound FOR WHAT IT DOES. Issue is authorization, not authentication.

### 2. Authorization — FAIL (no per-tool separation)
- All 9 tools share one bearer token. `get_queue_status` (read-only) and `run_shell` (arbitrary
  command execution) require identical credentials.
- No caller identity beyond IP address. No scoped tokens. No role-based access.
- **Severity: CRITICAL** — a leaked or stolen token grants full run_shell access.

### 3. Approval Gates — FAIL (none)
- `run_shell` executes `subprocess.run(cmd, shell=True)` with no filtering, no allowlist,
  no approval gate, no action_gate integration.
- 30-second timeout is the only constraint. No command blocklist.
- **Severity: CRITICAL** — equivalent to SSH as user lumen with a password.

### 4. Secret Isolation — PASS
- Token loaded from env var, not hardcoded. Burned literal `lumen-gateway-2026` refused.
- Token not logged. Drop-in config keeps the value out of the unit file.

### 5. Network Boundaries — FAIL
- Listens on `0.0.0.0:3333` — all interfaces, not just loopback or tailnet.
- CORS `allow_origins=["*"]` — any web origin can call it with the token.
- No IP allowlist. No Cloudflare Access guard (unlike memory-mcp which has one).
- **Severity: HIGH** — any device on the LAN can reach port 3333.

### 6. Filesystem Boundaries — FAIL (via run_shell)
- `run_shell` runs as user lumen with full home directory access.
- Can read `.secrets/`, write to any lumen-owned path, run any binary.
- No sandbox, no chroot, no seccomp, no PrivateTmp.

### 7. Audit Trail — FAIL
- `log.info(f"Tool called: {tool_name} by {request.client.host}")` — tool name + IP only.
- **Arguments are not logged.** A destructive run_shell leaves no record of WHAT was executed.
- No structured audit log. No log rotation. Output to systemd journal only.
- **Severity: HIGH** — forensic blindspot.

### 8. Redaction — FAIL
- No output masking. `run_shell` returns raw stdout+stderr (up to 2000 chars).
- Credentials, database URLs, tokens in command output pass through unredacted.
- No integration with `mask-secrets` or `dc-handles-scrub`.

### 9. Tool Discovery — PASS (benign)
- GET /mcp and tools/list both auth-gated. Tool list includes run_shell (design concern, not a bug).

### 10. Failure Handling — PARTIAL
- Tool exceptions caught, returned as isError: True with error message.
- Startup fails closed on bad token (exit 78, RestartPreventExitStatus=78).
- No rate limiting. No circuit breaker.

## Stale References
- **Ghost CMS**: `tool_get_agent_status` probes port 2368. Ghost is retired.
- **Groq**: `tool_get_token_budget` reports `groq_used / 100,000`. Groq is retired.
- **LibreChat**: Comment says removed 2026-04-21 but Ghost probe remains.
- **Surface Pro tags**: `tool_add_task` defaults tags to `["linux", "surface-pro"]`.
- **lumen-agent-v2**: `tool_get_agent_status` checks this unit. lumen-agent is retired.

## Decision: RETIRE

Evidence:
1. Zero `.claude.json` references across the fleet.
2. Zero established TCP connections at audit time.
3. Claude Code agents use stdio MCP servers exclusively (fleet-memory via dbhub, docker,
   gitea, grafana, playwright — all via lumen_mcp_guard).
4. Tool set is relic of pre-fleet-tools era: task queue, Ghost CMS, Groq budget — all retired.
5. run_shell superseded by Commander MCP (blocklist, handle store, scrub timer) and
   shared-terminal (session logging).

**If any consumer is discovered before retirement**, the gateway needs:
- run_shell removed or gated behind action_gate
- Per-tool token scopes (read vs write vs admin)
- Argument logging with redaction
- CORS restricted to tailnet origins or removed
- Listen address changed to 127.0.0.1 or tailnet IP only
- Stale references removed

---

## memory-mcp 403 Diagnosis (wq#4874)

### Root Cause
The 403 on /mcp has TWO causes:
1. **GET /mcp**: supergateway 4.1.0 serves POST JSON-RPC only. GET returns 403. Expected behavior.
2. **POST /mcp**: The access guard drop-in (`30-wq2768-access-guard.conf`) enforces Cloudflare
   Access JWT validation. Guard fails DNS resolution for `lumenseyes.cloudflareaccess.com`
   (`EAI_AGAIN`), so in `enforce` mode it rejects ALL HTTP requests to /mcp. /health bypasses guard.

### Access Paths

| Consumer | Transport | Hits 403? | Status |
|---|---|---|---|
| Claude Code (fleet-memory) | stdio via dbhub + lumen_mcp_guard | No | Working |
| HTTP callers to 127.0.0.1:3104 | POST JSON-RPC via supergateway | Yes (guard) | Blocked by DNS |
| mcp_gateway.py | Does NOT call memory-mcp | N/A | N/A |

### mcp_ro Role Verification

Role: mcp_ro (canlogin=t, createdb=f, createrole=f)
Grants: SELECT ONLY on 8 tables:
  fleet_facts_active, memory_events, memory_sources, recipes,
  retired_agent_episodes, retired_fleet_decisions, retired_fleet_devices, retired_fleet_facts

Tested and DENIED:
  INSERT INTO memory_events -> "cannot execute INSERT in a read-only transaction"
  CREATE TABLE -> "cannot execute CREATE TABLE in a read-only transaction"
  DROP TABLE -> "cannot execute DROP TABLE in a read-only transaction"

Read-only enforcement is LAYERED:
1. PostgreSQL role: SELECT grants only
2. dbhub launch.sh: READONLY=true MAX_ROWS=500
3. lumen_mcp_guard: --allow execute_sql,search_objects (tool allowlist)
4. DSN built from 0600 password file, never on argv

### Resolution
The 403 is not a bug — it is the access guard working as designed on an HTTP surface
with no live consumers. Claude Code uses stdio, bypassing the entire HTTP + guard stack.
If HTTP access is ever needed: fix DNS for lumenseyes.cloudflareaccess.com from Lumen,
or add a CF Access service token for programmatic callers.
