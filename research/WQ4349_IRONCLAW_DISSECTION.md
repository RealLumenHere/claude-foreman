# wq#4349 — Dissection: IronClaw (NEAR AI)

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Sources:** NEAR AI launch blog
(near.ai/blog/near-ai-launches-ironclaw-confidential-gpu-marketplace-and-multimodal-confidential-inference),
`github.com/nearai/ironclaw` (commit 29582a9, "docs: align security and sandboxed-tools
pages with real schema"), IronClaw docs mirrors — `docs.ironclaw.com/capabilities/sandboxed-tools`
(zh), `mintlify.wiki/nearai/ironclaw/security/credentials`,
`mintlify.wiki/nearai/ironclaw/security/network-security`,
`mintlify.wiki/logicminds/ironclaw/security/data-protection` — all fetched via
`fleet-search web` 2026-10-19 (SearXNG engines all suspended/CAPTCHA'd; DeepSeek Search
fallback served results; `gh`/`fleet-search github` both lack a usable token here, so this
is search-snippet evidence, not a direct repo checkout — flagged per snippet below).

Companion to `research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md` and the existing Limpet
plugin-security line: `design/wq3810_limpet_plugin_system.md`,
`design/limpet/plugin-manifest.schema.json`, `design/wq4056_limpet_plugin_supply_chain_trust_gate.md`,
`design/wq4224_limpet_capabilities_v1.md`, `design/wq4225_limpet_capability_filtered_tool_exposure.md`.
IronClaw is NEAR AI's from-scratch Rust reimplementation of OpenClaw (announced at NEARCON
2026 alongside a Confidential GPU Marketplace), repositioning OpenClaw's "always-on agent"
pitch around hardware-enforced security (TEE deployment) and local-first privacy.

---

## 1. WASM sandbox for untrusted tools

Untrusted tools run as WASM modules inside `wasmtime`. The host exposes a small, fixed set
of host functions (logging, time, workspace) and *nothing else* — no syscalls, no
environment access, no ambient network. Every other capability a tool needs (network
hosts, filesystem paths, which secrets get injected) must be declared in a sidecar
`capabilities.json` next to the `.wasm` binary — the single source of truth for what the
tool is allowed to do; nothing is granted ambiently.

Three independent resource bounds on the sandbox itself, each a trap/terminate, not a
graceful degrade:

- **Fuel metering.** Every WASM instruction burns fuel; exhaustion terminates the module.
  Default `WASM_FUEL_LIMIT=100000000`, overridable by env var. Kills infinite loops without
  a wall-clock timer race.
- **Memory limit.** Fixed linear memory; over-limit access traps and terminates. Default
  `WASM_MEMORY_LIMIT=16777216` (16 MiB).
- **Per-tool rate limiting.** `"rate_limit": {"requests_per_minute": 60, ...}` in
  `capabilities.json` — declared per tool, not global, so one noisy tool can't starve
  others' budget.

Request flow (docs' own diagram, translated): LLM picks a tool → host loads the WASM
module (cache or disk) → module runs inside the wasmtime sandbox → any network call goes
through the *host's* HTTP proxy, never a raw socket the module owns → proxy checks the
domain allowlist from `capabilities.json` → proxy decrypts and injects the matching
credential from encrypted storage → response returns to the module → output passes through
a "Safety Layer" that scrubs leaked secrets before the LLM ever sees the result.

The structurally important property: the tool *never holds a raw socket or a secret in its
own memory*. Enforcement is a host-side proxy sitting between the sandbox and the network,
not a check the WASM code is trusted to perform on itself.

## 2. Credential protection — proxy-injected, domain-scoped, output-scrubbed

Secrets are declared by name, not by value: `capabilities.json` lists
`"secrets": {"allowed_names": ["openai_api_key"]}` plus an injection spec describing *how*
to attach the secret to an outbound request — one of:

- `AuthorizationBearer {}` → `Authorization: Bearer <secret>`
- `AuthorizationBasic {username: "api"}` → `Authorization: Basic <base64(username:secret)>`
- `Header {name: "X-API-Key", prefix: Some("Bearer ")}` → custom header
- `QueryParam {name: "api_key"}` → `...?api_key=<secret>`

Validated flow for a call the WASM module makes (`http_request("https://api.openai.com/v1/chat/completions", ...)`):
host checks the allowlist allows that endpoint → finds a credential mapping for
`api.openai.com` → decrypts `openai_api_key` from the secrets store → injects
`Authorization: Bearer sk-proj-...` → executes the HTTP call → the WASM module receives
only the response, after the leak scanner runs on it. **The module never sees the secret
value, only the fact that a credential-bearing request succeeded or failed.** The
capabilities file names *which* secret may be used on *which* host; it cannot smuggle a
secret to an undeclared destination because the proxy — not the module — holds the mapping.

Secrets at rest are in an encrypted `secrets` table (AES-256-GCM per the ticket's framing;
the data-protection doc snippet confirms "Secrets: Encrypted in local database" without
re-stating the cipher, so treat AES-256-GCM as the ticket's prior claim, not something this
pass independently re-verified against source — flagged, not fabricated: it matches the
credentials-doc's "decrypts" framing and NEAR AI's TEE-based confidentiality pitch, but the
exact AEAD primitive wasn't visible in the snippets this pass could reach).

## 3. Network allowlisting — defended against the usual bypasses

The allowlist validator (`network-security` doc) matches host + `path_prefix` + HTTP
method, and explicitly defends the matcher against the bypasses that make allowlists leaky
in practice:

- **Scheme pinning.** `http://api.example.com/data` → denied (insecure scheme) even if the
  host is allowlisted; only `https://` passes.
- **Path normalization before matching.** `https://api.example.com/v1/../admin/users`
  normalizes to `/admin/users` before the `path_prefix: "/v1/"` check runs, so the
  dot-segment traversal is caught rather than string-matched against the raw path.
- **IPv6 literal normalization.** `[2001:db8::1]` has its brackets stripped before matching
  so a bracketed literal can't slip past a bracket-naive comparison.
- **Credential-in-URL rejection.** The snippet's own validator code fragment checks
  `parsed.username().is_empty() || parsed.password().is_some()` — a URL carrying
  `user:pass@host` is rejected outright, closing the classic allowlist bypass where a
  attacker-controlled userinfo segment tricks a naive host parser.
- **Response/request size caps** declared per tool in `capabilities.json` (example values:
  512 KB request / 1 MB response) — bounds exfiltration volume even through an allowed,
  correctly-authenticated channel.

## 4. Local encrypted storage — PostgreSQL, no cloud dependency

Local-first by construction, not by a toggle: database is local PostgreSQL (default) or a
libSQL file; vector search uses the `pgvector` extension rather than an external embedding
API; tool execution is the local WASM runtime above; secrets live encrypted in the local
database. Per the data-protection doc, *every* durable artifact is a local table —
`conversations`/`messages`, `workspace_entries`/`memory_chunks` (vector search), `secrets`,
`jobs`/`job_events`/`llm_calls` (full audit trail), `settings`. No telemetry pipeline, no
managed-cloud dependency for any of these. PostgreSQL is positioned as the desktop/
multi-user/high-volume backend; libSQL is the lighter-weight alternative, not a hosted
fallback.

## 5. Zero outbound by default

The stated policy: "The only external connections are to LLM providers (OpenAI, Anthropic,
etc.) when you explicitly send a message. You control which provider via configuration."
Combined with §1/§3, this reads as the same allowlist mechanism applied recursively: the
*agent's own* LLM-provider egress is itself a configured, user-chosen destination, not a
hardcoded phone-home — and every *tool's* egress goes through the same proxy+allowlist as
any other WASM capability. There is no separate "house" telemetry channel exempted from the
capability model; the model doesn't special-case its own vendor's traffic.

## 6. Where this sits vs. OpenClaw

The launch framing: IronClaw "carries forward the original OpenClaw vision — autonomous
agents that can act, transact, and coordinate on your behalf" while rebuilding the runtime
in Rust, moving deployment into TEEs on NEAR AI Cloud, and tightening the security model
from (implicitly) ambient/trust-based tool execution to the capability-declared WASM
sandbox described above. The pitch is explicitly "policy-based trust" (OpenClaw, inferred)
→ "cryptographic guarantees" (IronClaw): hardware-enforced isolation (TEE) plus
capability-enforced tool execution (WASM + proxy) rather than a runtime that trusts tool
code to self-police.

---

## 7. Gap check against current Limpet plugin model

`design/limpet/plugin-manifest.schema.json` today has no network or secrets axis at all:
the top-level schema is `"additionalProperties": false` (plugin-manifest.schema.json:8)
over a fixed key set — `plugin`, `trust`, `capabilities`, `mcp`, `paths`, `health`,
`dependencies`, `integrity`, `provenance`, `signature`, `hardware`
(plugin-manifest.schema.json:9–152) — and the one `capabilities` block that exists is a
free-form `additionalProperties: {"type": ["boolean","string","array"]}` grab-bag
(plugin-manifest.schema.json:43–51) covering `architectures`/`input_formats`/
`output_formats`: tool *shape*, not tool *permission*. A manifest declaring a network host
or a secret name would currently fail schema validation outright (no such key exists to
declare it into).

Correspondingly, `limpet/limpet_tools.py` has no execution-time sandbox boundary: plugin
code is fetched (`_clone_pinned`, limpet_tools.py:167), hash/signature-checked at install
(`_cmd_verify`, limpet_tools.py:312; the wq#4056 `integrity`/`signature`/`provenance` fields
above), and trust-tier gated (`_cmd_elevate`, limpet_tools.py:338) — all *install-time*,
supply-chain-shaped checks (did this payload come from where it claims, is it unmodified,
is it promoted past community tier). None of that is a *runtime* boundary: once a
`vetted`/`core` plugin is enabled, its actual tool invocation is native-process execution
(`_git` via `subprocess.run`, limpet_tools.py:55–56, is the pattern for any plugin that
shells out) with no per-call network allowlist, no per-call secret scoping, and no sandbox
resource cap (fuel/memory/rate) analogous to §1. Limpet's current gate answers "is this
plugin trustworthy to enable at all" (wq#4056's supply-chain question); it has no mechanism
yet for "what, specifically, is this enabled plugin allowed to touch on this call" (IronClaw's
`capabilities.json` question) — the two are complementary, not overlapping, and today only
the first exists.

## 8. Patterns applicable to Limpet (LIMPET VALUE)

The ticket's framing is right: the capability-manifest *model* — declare exactly which
tools and network endpoints an agent/plugin needs, enforce at the sandbox boundary, deny
everything undeclared — is the permission model Limpet's plugin system is missing, and it
layers cleanly on top of (not instead of) the existing supply-chain gate:

1. **Add a `network`/`secrets` capability block to `plugin-manifest.schema.json`**,
   sibling to the existing `capabilities` key (plugin-manifest.schema.json:43), shaped like
   IronClaw's: allowed hosts + path prefix + HTTP method + size caps, and `allowed_names`
   for secrets with an injection spec (bearer/basic/header/query — §2). This is additive to
   the `additionalProperties: false` top level (plugin-manifest.schema.json:8), not a
   rewrite.
2. **Enforce it with a proxy, not a self-check.** The structural lesson from §1/§2 is that
   the module (plugin/RE-tool code) must never hold the raw secret or a raw socket — a
   host-side broker validates against the declared allowlist and injects credentials after
   the fact. For Limpet this means the registry/meta-server (the existing MCP meta-server
   lane, wq#3808/wq#3815) is the natural broker position: tool calls that need network or
   secrets route through it, never direct from plugin code.
3. **Defend the allowlist matcher against the named bypasses (§3) up front**, since they're
   cheap to get wrong and expensive to fix after plugins start depending on lenient
   matching: scheme-pin to HTTPS, normalize path traversal before prefix match, strip IPv6
   brackets before match, reject URLs carrying embedded userinfo (`user:pass@host`).
4. **Bound untrusted plugin execution with fuel/memory/rate caps** if/when Limpet moves
   RE-tool plugins into a WASM or similarly sandboxed runtime (not required for today's
   native-subprocess model, but the resource-bound pattern — trap-and-terminate, not
   graceful degrade, §1 — is worth carrying into whatever sandbox Limpet eventually adopts
   for community-tier plugins specifically, since those are the tier wq#4056's trust gate
   already treats as unpromoted/inert by default).
5. **Scope the capability declaration to the call, not the plugin.** IronClaw's
   `capabilities.json` is per-tool-binary, sidecar to the `.wasm` file — the Limpet
   equivalent is per-plugin-manifest (already the right granularity:
   `plugin-manifest.schema.json` is one file per plugin), so this is a schema addition, not
   a new file-per-call scheme.
6. **Treat the agent's own LLM-provider egress as just another allowlisted destination**
   (§5) rather than a special-cased exemption — consistent with Limpet's existing
   zero-trust-by-default posture elsewhere in the capability work (`design/wq4224...` §4,
   safety class may only tighten, never loosen).

**Non-adoption:** PostgreSQL-as-storage-backend and TEE deployment are infrastructure
choices tied to IronClaw's own hosting model (NEAR AI Cloud), not permission-model patterns
— out of scope for what this ticket asked to extract (the capability-manifest / sandbox-
boundary model), and not evaluated here as a Limpet storage recommendation.
