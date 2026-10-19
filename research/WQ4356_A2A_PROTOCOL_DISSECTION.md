# wq#4356 — Dissection: Google A2A (Agent2Agent) protocol for multi-agent RE

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Sources:** a2a-protocol.org / a2aproject
GitHub specification (v1.0.0, Linux Foundation), agent2agent.info core-protocol guide,
authzed.com A2A guide, safeguard.sh A2A security analysis, api7.ai gateway-layer analysis,
A2ABreak paper (Lotfi et al., 2026) via agentpatterns-ai anti-pattern writeup, tech.yahoo.com
compliance-label piece — fetched via `fleet-search web` 2026-10-19 (SearXNG general engines
suspended/CAPTCHA'd; DeepSeek Search fallback used, per the tool's own `note` field). Doc
URLs inline per section.

Companion to the sibling LIMPET dissections (`research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md`,
`research/WQ4335_META_MUSE_DISSECTION.md`). Grounds against the planned Limpet multi-agent RE
loop in `design/wq3999_rex_skill_dissection.md:131-182` (the "7-agent recon / 5-blind-hunters /
reconcile topology") and the existing deterministic gate in `limpet/critic.py:1-33` — the
agent core itself is still design-only (`limpet/critic.py:18-19`: "Limpet's agent core is
still design-only, `design/wq3806_limpet_architecture.md` §6: model routing via LiteLLM is
not yet built"), so this is protocol research to shape that build, not a retrofit onto running
code.

---

## 1. Three-layer protocol

Source: https://agent2agent.info/specification/core/ ("Core Protocol Specification");
normative source is `specification/a2a.proto` (Protocol Buffers v3) — generated JSON and the
markdown spec are convenience outputs, SDK bindings are generated from proto, not hand-edited.

- **Layer 1 — canonical data model.** `AgentCard`, `Task`, `Message`, `Artifact`, `Part`.
- **Layer 2 — abstract operations.** `SendMessage`, `SendStreamingMessage`, `GetTask`,
  `ListTasks`, `CancelTask`, `SubscribeToTask`, the push-notification-config CRUD set,
  `GetExtendedAgentCard`.
- **Layer 3 — protocol bindings.** JSON-RPC 2.0 over HTTP(S), gRPC over HTTP/2, HTTP+JSON
  (REST-like, built on AIP-127 transcoding). All three bindings "must provide identical
  functionality, consistent behavior, equivalent authentication, and mapped error handling"
  (`tangled-skills/misc/a2a-1-0-0/reference/03-protocol-bindings.md`). Any abstract operation
  can run over any binding against the same data model — layers compose independently, which
  is why a profile like AAP (`auto-agent-protocol/a2a-profile.md`) can constrain layer 1 only
  and leave 2/3 untouched.

**Negotiation is static, not a handshake.** An `AgentInterface` entry in the card declares
`url` + `protocolBinding` (`JSONRPC`/`GRPC`/`HTTP+JSON`) + `protocolVersion`; a single agent
can list the same functionality three times, once per binding, at different URLs. The client
**MUST** pick the first supported binding in the card's own ordered list — the client reads
the card to resolve the transport, there is no runtime "which protocol do you speak" exchange
(a2aproject-github-io spec §4.4.6, §15 relationship-to-MCP section).

## 2. Agent Card discovery — `.well-known/agent-card.json`

Spec: §8.2 (well-known URI), §14.3 (IANA registration template, submitted not yet assigned),
§3.1.11 (extended card).

- **Registry-free by default.** `GET https://{domain}/.well-known/agent-card.json` MUST
  return an `AgentCard`. A client needs only the domain to bootstrap discovery — no central
  registry required. §8.2 lists two alternates (Registries/Catalogs, Direct Configuration) for
  cases where well-known isn't viable, but the well-known path is the one being standardized
  through IANA.
- **Card contents:** identity (`name`, `description`, `provider`, `version`), one or more
  `supportedInterfaces`/`AgentInterface` entries (endpoint + binding + version + optional
  `tenant`), `capabilities` (`streaming`, `pushNotifications`, `extendedAgentCard` booleans,
  `extensions` array), `defaultInputModes`/`defaultOutputModes` (MIME types), `skills[]`, and
  `securitySchemes` (what auth the endpoint expects — not whether the caller has it).
- **Two-tier capability surface.** If `capabilities.extendedAgentCard` is true, an
  authenticated `GET /extendedAgentCard` MAY return a richer card — more skills, more detail —
  gated by whatever the public card's `securitySchemes` declares. Clients that fetch the
  extended card SHOULD replace the cached public one for the session (§3.1.11). This is the
  direct mechanism for "hunters discoverable, not hardcoded": a recon agent (or an operator)
  can enumerate skills from one well-known fetch instead of a config file wired at deploy
  time, and a privileged caller can see more of the skill surface than an anonymous one
  without a second protocol.
- **Optional signing.** Cards MAY carry a JSON Web Signature over canonicalized JSON (JCS);
  clients SHOULD verify when present but nothing mandates it (api7.ai gateway-layer piece).
  A valid signature proves card integrity and who published it — it proves nothing about
  whether the advertised skills are truthful or the agent is safe (same source; also
  A2ABreak, §"Signing the Agent Card does not close this").

## 3. Canonical data model

Entities per `specification.md` (a2aproject/A2A, `docs/specification.md` §4.4.x) and the
agent2agent.info summary:

- **`AgentCard`** — the discovery document above.
- **`Task`** — the durable unit of work. Server-generated `id` (clients must not invent task
  IDs for creation, agent2agent.info "Key v1.0 updates"), `contextId` (groups multiple Tasks
  and Messages into one conversational thread — the mechanism a fleet orchestrator uses to
  track a multi-step delegation without re-establishing identity per hop), `status` (an
  explicit state machine, §4 below), `history` (prior Messages), `artifacts[]`.
- **`Message`** — one conversational turn inside a Task. `role` (`user`/`agent`), `parts[]`,
  a `messageId`.
- **`Part`** — the atomic content unit inside a Message or Artifact: `TextPart`, `FilePart`,
  `DataPart` (structured JSON, discriminated by a `kind` field — the legacy draft's `type`
  field is gone in v1.0 and a client sending it gets a hard parse error under
  `DisallowUnknownFields`, deliberately: v1.0 conformance over Postel's-law leniency, per the
  `jazz1x/rallish` Go binding doc).
- **`Artifact`** — a completed Task's deliverable: a named, structured output composed of
  `Part`s (generated code, an analysis result, transformed data). Artifacts are what a Task
  *returns*; Messages are what happens *during* it.
- **`Extension`** — declared in `AgentCard.capabilities.extensions[]` and signaled per-request
  via an `A2A-Extensions` header (JSON-RPC/REST) or `a2a-extensions` gRPC metadata key. This is
  the sanctioned way to carry domain-specific payloads (e.g. the A2UI UI-generation extension
  seen in LangChain's A2A server docs) without forking the core schema.

## 4. Abstract operations

Mapping, JSON-RPC method / gRPC method / REST endpoint
(`tangled-skills/misc/a2a-1-0-0/reference/03-protocol-bindings.md`,
`a2aproject/A2A/docs/topics/transport-table.md`):

| Operation | JSON-RPC | REST |
|---|---|---|
| Send message | `SendMessage` | `POST /message:send` |
| Send streaming message | `SendStreamingMessage` | `POST /message:stream` (SSE) |
| Get task | `GetTask` | `GET /tasks/{id}` |
| List tasks | `ListTasks` | `GET /tasks` |
| Cancel task | `CancelTask` | `POST /tasks/{id}:cancel` |
| Subscribe to task | `SubscribeToTask` | `POST /tasks/{id}:subscribe` (SSE; errors on terminal tasks) |
| Push-notification config CRUD | `{Create,Get,List,Delete}TaskPushNotificationConfig` | `/tasks/{id}/pushNotificationConfigs[...]` |
| Get extended card | `GetExtendedAgentCard` | `GET /extendedAgentCard` |

- **Task state machine** (§2.2, §3.4.1): `submitted`/`working` (in flight) → `input-required`
  / `auth-required` (interrupted — the task is *not* failed, it's parked waiting for the
  client to supply more data or a credential, resumable by `taskId`) → `completed` / `failed`
  / `canceled` / `rejected` (terminal). v1.0 standardized the interrupt states so an
  orchestrator gets a deterministic signal to resume rather than inferring "stuck vs. working"
  from a timeout.
- **`CancelTask`** gives explicit cancellation — a client that no longer needs a result (or
  needs to abort a runaway sub-agent) doesn't have to let it run to completion or kill the
  transport.
- **`SubscribeToTask`/`SendStreamingMessage`** give progress streaming via SSE without
  polling: `SendStreamingMessage` opens a stream for a task just created; `SubscribeToTask`
  re-attaches to one already running (`tasks/resubscribe` in the transport table) — the
  distinction matters for a long RE job a client disconnected from and wants to reattach to.
  Three delivery patterns total: polling (`GetTask`), streaming (SSE, requires
  `capabilities.streaming: true`), push notifications (server-initiated webhook POST,
  requires `capabilities.pushNotifications: true`).
- **Three bindings, one semantics.** Error handling maps consistently across bindings:
  JSON-RPC uses standard `-32700..-32603` plus A2A-specific `-32001..-32009`; REST maps the
  same error model onto RFC 9457 Problem Details (`application/problem+json`).

## 5. Opaque execution — collaborate on capability, not state

Source: safeguard.sh A2A security analysis, agentpatterns-ai/website A2UI pattern doc, Google
A2A launch post (via agentpatterns-ai summary).

A2A's stated core premise: agents from different organizations/frameworks coordinate without
revealing internal state. When agent A delegates to agent B, "B's reasoning, memory, prompts,
and tool calls are not exposed to A. The only thing A sees is the result B chose to return."
Google frames this as *intentional* — handing a task to a specialist agent "avoids polluting
the delegating agent's context and preserves data privacy" (developers.googleblog.com, via
agentpatterns-ai) — not an accidental limitation to work around.

Consequence for a Limpet-shaped fleet: this is exactly the isolation
`design/wq3999_rex_skill_dissection.md:141-147` demands of the five stance agents
(`re-bughunt`, `re-safety`, `re-arithmetic`, `re-lifecycle`, `re-logic`) — each reads the same
recon-produced evidence but "may not see another's findings" (`re-safety.md:28` per that
doc). A2A's opaque-execution model is a protocol-level version of the same rule: a hunter
cannot see another hunter's raw context, and the protocol's answer to "then how do they
collaborate" is the same as REx's — exchange declared capability (skills in the Agent Card)
and the data a Task/Artifact actually carries, never shared memory. The tradeoff is also the
same one noted at `wq3999_rex_skill_dissection.md:156-160`: opacity is a feature for
independence but a cost for audit — safeguard.sh notes "A cannot audit what B did with the
data A sent," which is the protocol-level restatement of "one leak collapses five opinions
into one held five times."

## 6. CAVEAT — A2A deliberately omits authorization

This is the single most load-bearing fact in the spec for any fleet planning to adopt it, and
every source pulled agrees on it independently:

- **It's a scoping decision, not a gap to be fixed later.** authzed.com: "Authorization and
  security is intentionally left as an 'implementation-specific' decision." api7.ai: "The A2A
  server must authenticate incoming requests and authorize operations based on the
  authenticated identity. Authorization remains implementation-specific because only the
  server knows which skills, data, and actions a caller may use." The spec supplies
  `securitySchemes` (OpenAPI-parity: API key, HTTP auth/Bearer, OAuth2, OIDC, mTLS) so a server
  can *declare* what it expects — it does not supply a policy model for what an authenticated
  caller is then allowed to do.
- **Identity is a property of the connection, not the message — and it doesn't survive a
  delegation hop.** From the A2ABreak analysis (Lotfi et al., 2026, via
  agentpatterns-ai/website anti-pattern doc): "the specification states that payloads do not
  carry user or client identity directly and that identity is established at the transport
  layer (spec §7.2)... authentication is strictly hop-by-hop... each agent knows only its
  immediate caller's identity." Two concrete failure shapes follow directly: (1) an
  intermediate agent can silently harvest and replay what it forwards; (2) no downstream agent
  can key a policy decision on the *original* requester, because "no delegation context token,
  principal identity field, or chain-of-custody primitive exists in the protocol." A signed
  Agent Card doesn't close this either — a signature "authenticates identity but does not
  attest capability: it confirms who published the card, not whether the advertised skills are
  truthful."
- **Card discovery is itself an attack surface, unauthenticated by default.** safeguard.sh and
  the Tencent/Aliyun pieces (independently, from a China-market security-research angle) both
  flag Agent Card poisoning: a malicious or typosquatted card can redirect an orchestrator's
  routing, or smuggle prompt-injection into the discovery phase itself (plain-text, structured
  JSON, and natural-language social-engineering variants were all tested successfully against
  unguarded orchestrators per the Tencent piece's cited IEEE study). Regex-level cleaning
  caught the syntactic attempts but not the natural-language ones; an LLM-based card auditor
  caught all tested attempts at pilot scale (n=3/tier/combo — explicitly flagged as too small
  to generalize from).
- **The compliance label is narrower than it sounds.** tech.yahoo.com: being "A2A-compliant"
  certifies wire-level interoperability and a self-described Agent Card — it does not certify
  identity (card signing is optional), does not certify a governance/registry layer (A2A GitHub
  Discussion #741 closed without shipping one, deferred to separate AI-Catalog/ARD specs), and
  does not certify agent conduct ("compliance certifies the conversation, not the conduct").

**Bottom line for Limpet:** treat every A2A primitive in this document (Agent Card, Task,
Artifact, the operation set) as a **transport and discovery contract only**. RE authorization
— who may dispatch a hunter at a given target/scope, who may approve a reconcile verdict,
what evidence a sub-agent is allowed to see — has to be a separate enforcement plane that sits
*on top of* A2A, the same posture the fleet already applies to Claude Code's own permission
model (`research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md` §2: "Planning is a *permission state*,
not an architectural phase"). This matches Lumen's own instinct 3/CLAUDE.md framing — A2A
would give the fleet a wire format for "what is this task and who's the counterparty," never
a substitute for the broker/allowlist/gate layer that decides whether the task should run.

## 7. LIMPET VALUE — what to adopt, what to build separately

**Adopt as contract shape** between a recon agent, the stance/hunter agents, and reconcile
(`design/wq3999_rex_skill_dissection.md:131-182`):

1. **Agent Card** → the skill-advertisement shape for each stance agent (`re-bughunt`,
   `re-safety`, `re-arithmetic`, `re-lifecycle`, `re-logic`): `skills[]` with
   `tags`/`examples`/per-skill `inputModes`/`outputModes` gives a machine-readable way for a
   dispatcher to match a target class to the stances that apply (§4.3 extraction spec item 2 in
   wq3999: "add arithmetic/lifecycle/logic by target class") instead of hardcoding which
   stances run on which target. "Hunters discoverable not hardcoded" (ticket's own phrasing)
   is literally the well-known-card discovery pattern in §2 above.
2. **Task** → the durable unit per stance run: one Task per (target, stance) pair, `contextId`
   grouping all Tasks for one target's analysis loop — gives `ListTasks`-style visibility into
   "which stances have reported for this target" without a bespoke status table.
3. **Artifact** → the finding schema's output container
   (`design/wq3999_rex_skill_dissection.md:184-192`: source + sink + broken-guard +
   affected-principal) — a stance's completed Task returns its findings as an Artifact, not as
   mutated shared state, which is what keeps the isolation in §5 real rather than nominal.
4. **Operations** → `SendMessage` to dispatch a stance against recon's evidence set,
   `SubscribeToTask`/streaming for progress on long-running emulation/fuzzing stances,
   `CancelTask` to abort a stance that's run past budget, `GetTask`/`ListTasks` for reconcile
   to poll status before it adjudicates (`limpet/critic.py`'s deterministic gate runs inside
   reconcile's "take it" branches per wq3999 item 3 — it needs to know a stance's Task is
   actually `completed` before treating its Artifact as adjudicable).

**Build separately, outside A2A:**

- **Authorization and approval gates.** §6 above — A2A gives the wire format, never the policy.
  Who may launch a hunter at a given scope, what evidence a stance is allowed to read, who
  signs off on a reconcile verdict before it becomes a committed finding: that's the fleet's
  own broker/allowlist/gate plane (parallel to the per-command Bash allowlist and PreToolUse
  guard this very worker runs under), not something to delegate to `securitySchemes`.
- **Delegation-chain identity.** If Limpet's dispatcher ever sits more than one hop from the
  stance agent (e.g. a future cross-node fleet dispatch), the hop-by-hop identity loss in §6
  applies directly — don't assume a stance Task's caller identity is the original requester
  without an explicit chain-of-custody field the fleet adds itself; A2A doesn't supply one.
- **Card trust.** If stance "cards" are ever served over a network boundary (vs. today's
  in-process agent defs under `REx:claude-skill/agents/`), treat card poisoning as a real
  threat per §6 — sign cards, verify the signer against a fixed trust store, and don't let an
  orchestrator route to a card it hasn't pinned.
