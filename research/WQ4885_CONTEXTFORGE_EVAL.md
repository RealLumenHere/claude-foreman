# wq#4885 — EVALUATE: IBM ContextForge (MCP registry / gateway / federation)

**VERDICT: DEFER.** The ticket's own gate was "evaluate only if the gateway audit (wq#4873)
identifies material gaps that ContextForge uniquely fills." wq#4873 is done (PR#2637) and
closed every gap it found in-place, with fleet code. None of its six findings needs a
registry, a federation layer or an OpenTelemetry pipeline. No code changed here; this is a
research verdict plus the evidence behind it.

## What ContextForge is

Prior-art search (`fleet-search web "IBM mcp-context-forge gateway registry federation"`,
2026-11-18): `github.com/IBM/mcp-context-forge`, "an AI Gateway, registry, and proxy that
sits in front of any MCP, A2A, or REST/gRPC APIs, exposing a unified endpoint with
centralized discovery". Docs: `ibm.github.io/mcp-context-forge/`. Pitch: one endpoint that
federates many MCP/A2A servers and wraps REST/gRPC as MCP tools, with an admin UI, auth,
rate limits and OTel tracing.

## The wq#4873 gate came back closed

`ops/wq4873-mcp-gateway-audit-a1/README.md:8-15` lists the six findings and their fixes:

| wq#4873 finding | Fixed by | ContextForge-unique? |
|---|---|---|
| `run_shell` unrestricted | Tool removed | No — deletion beats gating |
| No per-tool auth | 3-tier read/write/admin, `mcp_gateway.py:102-109`, separate admin token | No |
| No argument audit trail | JSONL audit with redaction, `mcp_gateway.py:23-24,44-59` | No |
| CORS `*` | Localhost + tailnet regex, `mcp_gateway.py:79-80` | No |
| Stale Ghost/Groq refs | Removed | No |
| `utcnow()` deprecated | Replaced | No |

Its open question (`README.md:40-46`) runs the other way: `mcp_gateway.py` is not the fleet
MCP path at all and is a retirement candidate once mobile sessions move off it. The audit
argues for *fewer* gateways, not a bigger one.

## The other ContextForge features already have a fleet answer

- **Governance / per-tool policy.** The Claude Code MCP servers (gitea, postgres, docker,
  playwright, grafana, fleet_state, stealth_browser, council per
  `ops/wq4514-mcp-install-batch/install.sh:8-9`; ghidra-lumen per wq#4946) run behind
  `ops/wq4514-mcp-install-batch/lumen_mcp_guard.py:1-48`: fail-closed tool allowlist,
  argument pinning (`--arg-allow`, `--path-arg`, `--http-url-arg`), output masking, result
  caps, untrusted-output tagging, tool-drift detection, and a per-server JSONL audit
  (`:44`). That is stricter than a central registry's allow/deny: it pins argument values.
- **Centralized registry.** Every server is a stdio process on Lumen, declared in
  `.claude.json` and installed by `ops/wq4514-mcp-install-batch/install.sh`. One host, one
  config: nothing to discover.
- **Federation.** Every guarded Claude Code MCP server is installed on Lumen; Horus only
  relays Commander to Lumen (CLAUDE.md §3). There is no second MCP estate to federate with.
- **REST/gRPC-to-MCP.** No ticket in this lane asks to wrap an internal REST/gRPC API as MCP.
  The ones that exist (Gitea, Grafana, Docker) already have first-party MCP servers.
- **OpenTelemetry tracing.** Already chosen and scoped: `ops/wq1299-phoenix-tracer/README.md:1-15`
  puts Arize Phoenix (OTel-native, single container) on LiteLLM's OTLP callback, deliberately
  "bounded, not a platform build-out". For MCP calls the guard's JSONL rows already record
  who/what/when; spans add nothing a single-host stdio proxy needs.

## Cost of adopting it anyway

The MCP install batch runs under a freeze rule: "no service without naming what it replaces"
(`ops/wq4514-mcp-install-batch/install.sh:5`). ContextForge replaces nothing. It would be a
new long-running service (Python app + DB + admin UI) on Lumen, its own auth and secrets,
and a second policy layer in front of `lumen_mcp_guard`. Stdio servers would need to be
re-exposed over HTTP/SSE to sit behind it, which widens the network surface that wq#4873 and
the guard just shrank.

## Re-open trigger

Re-open only if one of these becomes true:

1. MCP servers run on a second node (Horus, Thoth) and agents there need one discovery
   endpoint for the whole fleet.
2. A ticket needs an internal REST/gRPC service exposed as MCP tools and no first-party MCP
   server exists for it.
3. A follow-up gateway audit finds a gap `lumen_mcp_guard` cannot close (for example, a
   cross-server rate limit or a shared tracing requirement).
