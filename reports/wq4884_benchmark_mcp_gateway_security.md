# wq#4884 — Benchmark: Docker MCP Gateway vs Lumen mcp_gateway.py security controls

**Date:** 2026-11-18 · **Lane:** BUILD · **Scope:** Reference comparison, not a replacement
recommendation. Benchmarks Docker MCP Gateway (github.com/docker/mcp-gateway, Go,
containerized architecture) against the Lumen gateway audit findings. Docker MCP Gateway
facts are from model knowledge of the project's public documentation and architecture as of
early 2025 (pre-cutoff); unverifiable claims are flagged with `[model-knowledge]`.

---

## 1. Architecture summary

| Dimension | Lumen `mcp_gateway.py` | Docker MCP Gateway |
|---|---|---|
| Language | Python 3 / FastAPI / uvicorn | Go |
| Transport | HTTP + JSON-RPC 2.0 on `:3333` | HTTP/SSE, stdio proxy; supports streamable-HTTP |
| Auth | Bearer token (env var), constant-time `hmac.compare_digest` | OAuth 2.1 + PKCE, Bearer tokens, API keys; pluggable auth providers `[model-knowledge]` |
| Process model | Single uvicorn process, tools run in-process or `subprocess.run` | Containerized: each MCP server runs in its own container; gateway is the control plane `[model-knowledge]` |
| Deployment | systemd unit on bare metal (Lumen), Tailscale-only network | Compose-based; gateway + per-server containers `[model-knowledge]` |

---

## 2. Security control comparison

### 2.1 Container isolation

| Control | Lumen gateway | Docker MCP Gateway | Gap / parity |
|---|---|---|---|
| **Process isolation** | None — tools execute in the gateway process (`subprocess.run(cmd, shell=True)` for `run_shell`, in-process Python for all others). A tool crash or exploit compromises the gateway. (`mcp_gateway.py:239`) | Each MCP server runs in a dedicated container with its own namespace/cgroup. Gateway communicates over stdio/HTTP bridge. Tool crash is contained to that server's container. `[model-knowledge]` | **Docker ahead.** Lumen's `run_shell` with `shell=True` is the highest-risk surface: arbitrary command execution as `lumen` user with no namespace boundary. |
| **Filesystem boundary** | Gateway reads/writes host paths directly (`/home/lumen/agent/TASK_QUEUE.jsonl`, `MEMORY.md`, logs). `run_shell` has full `lumen` user filesystem access. | Containers get bind-mounted volumes only for declared paths. Server container cannot access host filesystem beyond its mounts. `[model-knowledge]` | **Docker ahead.** Lumen tools have implicit access to everything the `lumen` user owns. |
| **Resource limits** | None on the gateway process itself. Relies on the systemd unit's cgroup limits (if set). | Per-container memory/CPU limits via compose resource constraints. `[model-knowledge]` | **Docker ahead**, though Lumen's systemd unit can apply equivalent `MemoryMax`/`CPUWeight` (pattern already used: `ops/wq3299` applied `MemoryHigh=6G` to Bes failover). |
| **Network segmentation** | Gateway binds `0.0.0.0:3333`, protected by Tailscale ACLs only (no iptables rules on the port itself). Tools can make arbitrary outbound connections as `lumen`. | Per-container network: servers run on isolated bridge networks. Only the gateway container has external-facing ports. Backend servers have no direct inbound. `[model-knowledge]` | **Docker ahead.** Lumen relies entirely on Tailscale for network-layer isolation; a compromised tool has full outbound. |

### 2.2 Secret scoping

| Control | Lumen gateway | Docker MCP Gateway | Gap / parity |
|---|---|---|---|
| **Gateway auth token** | Single env var `MCP_GATEWAY_TOKEN` from `0600` file via systemd `EnvironmentFile`. Fail-closed: refuses startup if unset, burned, or <32 chars. Burned-token denylist in source. (`mcp_gateway.py:28-34`) | OAuth 2.1 with PKCE; supports multiple auth providers, per-client scoping. Token rotation built into OAuth flow. `[model-knowledge]` | **Docker ahead on flexibility.** Lumen's single-token model is correct (constant-time compare, fail-closed) but doesn't support per-client auth or token rotation without gateway restart. |
| **Secret injection to tools** | Secrets available to tools via the gateway process's full environment + filesystem. `run_shell` inherits all env vars. No per-tool secret scoping. | Secrets injected per-container via compose `secrets:` or env vars scoped to each server's container. Server A cannot read Server B's secrets. `[model-knowledge]` | **Docker ahead.** Lumen has zero secret scoping between tools. `run_shell` can `env` to dump every secret in the process. |
| **Secret leakage in logs** | Addressed by ops layering: `wq#1698` redirects supergateway body logs away from journald/vlogs, `wq#3300` does the same for Bes failover. The gateway itself logs only tool name + client IP (`mcp_gateway.py:307`), not arguments or results. | Gateway logs tool calls with configurable verbosity. Container stdout/stderr separation means per-server log routing is native. `[model-knowledge]` | **Parity.** Both require operational discipline. Lumen's log-redaction work (wq#1698, wq#3300) shows the problem has been hit and mitigated live; Docker's container-native log separation is cleaner by design. |

### 2.3 Tool logging and audit

| Control | Lumen gateway | Docker MCP Gateway | Gap / parity |
|---|---|---|---|
| **Call logging** | `log.info(f"Tool called: {tool_name} by {request.client.host}")` on success only (`mcp_gateway.py:307`). Error paths log tool name + exception message. No structured audit log, no request ID, no argument/result capture for audit. | Structured JSON logs per tool call with request tracing. Container-level stdout capture gives per-server audit separation. `[model-knowledge]` | **Docker ahead.** Lumen's logging is minimal — no structured audit trail, no correlation IDs, no argument capture (which is a tradeoff: argument capture risks leaking secrets, but the absence means no forensic trail for `run_shell` abuse). |
| **Auth failure logging** | `log.warning` with client IP on every auth refusal (`mcp_gateway.py:43`). | Auth failures logged with full request metadata, rate limiting, and optional alerting integration. `[model-knowledge]` | **Docker ahead on depth**, but Lumen's coverage is functional — wq#2748's test suite (`test_failclosed.sh:21`) confirms auth-refused requests don't reach tool execution. |
| **Watchdog / health** | `ops/wq2606`: timer-driven watchdog checks `/health`, restarts on failure. Pressure guard (`mcp-pressure-guard.sh`) verifies `MainPID` advanced post-restart. | Health endpoints per container; compose-level `healthcheck:` with restart policy. `[model-knowledge]` | **Parity.** Both have health monitoring with auto-restart. Lumen's `MainPID` verification (wq#3299) is a stronger liveness check than a bare HTTP probe. |

### 2.4 Tool discovery and authorization

| Control | Lumen gateway | Docker MCP Gateway | Gap / parity |
|---|---|---|---|
| **Tool registration** | Static Python list (`TOOLS`, `mcp_gateway.py:49-120`). Adding/removing tools requires code change and gateway restart. | Dynamic: servers register on startup via the gateway's server management API. Hot-add/remove without gateway restart. `[model-knowledge]` | **Docker ahead on flexibility**, but Lumen's static list means no rogue tool registration — the attack surface is fixed and auditable. |
| **Per-tool auth** | None. Any authenticated caller can invoke any tool, including `run_shell`. | Per-server and per-tool access policies configurable via gateway config. `[model-knowledge]` | **Docker ahead.** Lumen's `run_shell` being callable by any authenticated client is the single largest security gap. |
| **Input validation** | Minimal: tool handlers check for required args but no schema validation beyond JSON-RPC structure. `run_shell` accepts any string. | JSON Schema validation per tool's `inputSchema` enforced at gateway level. `[model-knowledge]` | **Docker ahead.** Lumen defines `inputSchema` in tool metadata but never validates against it server-side. |

---

## 3. What Lumen already does that Docker MCP Gateway doesn't address

| Lumen control | Detail | Docker equivalent |
|---|---|---|
| **Burned-token denylist** | `_BURNED_TOKENS` set in source (`mcp_gateway.py:30`) — tokens known to have been exposed are permanently refused, even if someone re-injects them. | No equivalent in standard Docker MCP Gateway config. `[model-knowledge]` |
| **Fail-closed startup** | Exit code 78 (EX_CONFIG) + `RestartPreventExitStatus=78` stops crash-loop on misconfiguration. Gateway doesn't start without valid auth. (`mcp_gateway.py:33-34`) | Compose restart policy handles crashes but doesn't distinguish config errors from runtime failures. `[model-knowledge]` |
| **Network-layer isolation (Tailscale)** | Gateway is reachable only over Tailscale (100.x.x.x), not the public internet. ACL-enforced at the network level. | Docker MCP Gateway assumes network config is external; no built-in VPN/mesh integration. `[model-knowledge]` |
| **Operational hardening genealogy** | Five rounds of live incident response (wq#829 handler leak, wq#1698 log leaks, wq#2748 auth bypass, wq#3299 OOM, wq#3527 loopback lockdown) have hardened the deployment incrementally, each with test coverage. | Out-of-box, not battle-tested against these specific failure modes. `[model-knowledge]` |

---

## 4. Risk ranking: Lumen gateway's open gaps

Ordered by severity, referencing what Docker MCP Gateway would close:

| # | Gap | Severity | Docker MCP Gateway would help? |
|---|---|---|---|
| 1 | `run_shell` executes arbitrary commands as `lumen` with `shell=True`, no allowlist, no namespace boundary (`mcp_gateway.py:234-243`) | **Critical** | Yes — container isolation would contain blast radius. But the real fix is an allowlist or removal of `run_shell`. |
| 2 | No per-tool secret scoping — every tool inherits the full process environment | **High** | Yes — per-container secrets are the Docker model's core advantage. |
| 3 | No per-tool authorization — any authenticated caller can invoke any tool | **High** | Yes — per-server access policies would scope this. |
| 4 | No input validation against declared schemas | **Medium** | Partially — Docker validates at gateway level, but custom tools still need their own. |
| 5 | Minimal audit logging (name + IP only, no arguments, no request ID) | **Medium** | Yes — structured JSON logging with request tracing. |
| 6 | No tool-level rate limiting | **Low** | Yes — per-container resource limits and gateway-level rate limiting. |

---

## 5. Recommendation framework (not a decision — reference for wq#4873)

**Replace:** not recommended. The Lumen gateway serves a specific, narrow scope (9 tools,
single-user Tailscale-only access, known operational failure modes already mitigated). The
Docker MCP Gateway's container-per-server model adds complexity (compose orchestration, image
management, container networking) that is overhead for this scale.

**Complement:** viable for specific controls.
- If `run_shell` must remain, running it inside a dedicated container with scoped mounts
  and no host network would close gap #1 without rewriting the gateway.
- Per-tool secret injection via compose secrets would close gap #2.
- Both can be achieved by wrapping individual high-risk tools in containers behind the
  existing gateway, rather than replacing the gateway itself.

**Harden in-place (lowest cost, highest ROI for current scale):**
- Remove or allowlist `run_shell` (gap #1) — this is the critical path.
- Add structured audit logging with request IDs (gap #5).
- Validate tool arguments against declared `inputSchema` server-side (gap #4).
- Add per-tool authorization scoping (gap #3) — even a simple
  `tool_name → required_scope` map in the auth check.

---

## 6. Methodology and limitations

- **Lumen gateway analysis:** direct code read of `ops/wq2748-mcp-gateway-failclosed/mcp_gateway.py`
  (334 lines, the live deployed version per wq#2748 README), plus operational context from
  `wq#829`, `wq#1698`, `wq#2606`, `wq#2748`, `wq#3299`, `wq#3300`, `wq#3527` ops bundles
  in this repo.
- **Docker MCP Gateway analysis:** based on model knowledge of the project's architecture
  and documentation as of early 2025. Claims marked `[model-knowledge]` have NOT been
  verified against the live project source this session. A follow-up with web access or a
  local clone of `github.com/docker/mcp-gateway` would firm up these claims.
- **wq#4873 (gateway audit):** not found in this repo as a completed deliverable. This
  benchmark was written against the raw code and operational history rather than a
  pre-existing audit document. If wq#4873 produces findings that contradict this benchmark,
  that audit (being code-verified) takes precedence over model-knowledge claims here.
