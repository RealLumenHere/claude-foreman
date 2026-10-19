# wq#4347 — Dissection: ClawSwarm director-worker delegation for Limpet multi-agent RE

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Source:** the published `claw-swarm`
package on PyPI (version 0.0.5, `https://pypi.org/project/claw-swarm/0.0.5/`), fetched via
`fleet-search web` 2026-10-19 (SearXNG general engines suspended/CAPTCHA'd; DeepSeek Search
fallback used, per the tool's own `note` field). **Scope honesty:** this dissects the project's
published README/description — its stated architecture, module names, agent roster, and CLI —
not decompiled source. WebFetch of the sdist is not on this worker's allowlist, so class
bodies and the actual proto file were not read; every name below (`HierarchicalSwarm`,
`SwarmSpec`, `run_claude_developer`, `claw_swarm.prompts`, `create_agent`, `clawswarm run`) is
quoted as the README presents it, and claims about internals are marked as such. Treat the
Rust-compilation and "production-ready" lines as vendor marketing until verified against code.

Companion to the sibling LIMPET dissections (`research/WQ4356_A2A_PROTOCOL_DISSECTION.md`,
which covers the *wire contract* for delegation; this one covers the *orchestration topology*).
Grounds against the planned Limpet multi-agent RE loop in
`design/wq3999_rex_skill_dissection.md:131-182` (the 7-agent recon / 5-blind-hunters /
reconcile topology) and the deterministic adjudication gate in `limpet/critic.py:12-28`. The
agent core is still design-only (`limpet/critic.py:18-20`: "Limpet's agent core is still
design-only, `design/wq3806_limpet_architecture.md` §6: model routing via LiteLLM is not yet
built"), so this is topology research to shape that build, not a retrofit onto running code.

---

## 1. Hierarchical swarm — director plans, workers execute, summarizer compresses

ClawSwarm's core agent is a **`HierarchicalSwarm`** (Swarms-framework primitive). One message
drives a three-role pipeline:

- **Director (`ClawSwarm`)** — receives the user message, **creates a plan**, and issues
  **structured orders (`SwarmSpec`)** to one or more workers. The director's own tools are
  "Plan + orders (structured output for the swarm)" — i.e. it does not do domain work itself,
  it emits a typed delegation object and routes. `SwarmSpec` is the load-bearing primitive: the
  director's output is not free text handed to a worker, it is a **structured order set** the
  framework dispatches. The README does not publish the `SwarmSpec` schema; from the role
  description it carries at least (worker-selection, per-worker task) tuples.
- **Specialist workers** — each has a focused role and a fixed tool set; the director chooses
  who runs:
  | Worker | Role | Tools |
  |---|---|---|
  | `ClawSwarm-Response` | greetings, short factual answers, clarifications | none (LLM only) |
  | `ClawSwarm-Search` | web / semantic search | via Exa |
  | `ClawSwarm-TokenLaunch` | launch tokens, claim fees on Swarms World (Solana) | `launch_token`, `claim_fees` |
  | `ClawSwarm-Developer` | implementation and debugging | via Claude Code (`run_claude_developer`) |
- **Telegram Summarizer** — a post-processing role, not a worker: turns the combined worker
  output into a concise, **emoji-free** reply for chat. It is a presentation-layer compressor
  sitting between the swarm's output and the channel.

**The pattern that matters for Limpet:** the director is a *pure router* — it holds the plan
and the SwarmSpec, owns no domain tool, and never does the specialist work. Scaling from one
agent to many is "add a worker to the roster + teach the director it exists"; the director's
own loop never changes. That is the opposite of a monolithic agent that grows a bigger tool
belt. Each worker is independently scoped (its own tools, its own prompt), which is the same
blast-radius argument Limpet's stance agents already make.

## 2. Multi-channel gateway — one gRPC ingest API normalizes every source

- **Messaging Gateway** — a centralized ingest that accepts messages from Telegram, Discord,
  and WhatsApp and **normalizes** them into one internal shape before the agent sees them.
  "One gRPC API for all supported channels; add or remove platforms without changing agent
  logic." The channel-specific adapter lives at the gateway edge; the swarm consumes a
  channel-agnostic message.
- **Replier** — the return path: after the swarm (and Summarizer) produce output, the Replier
  sends it **back to the originating channel**. The gateway preserves enough routing context
  (which channel/chat a message came from) for the Replier to answer the right place.
- **End-to-end flow:** `message on any channel → Gateway normalizes + exposes via gRPC →
  HierarchicalSwarm (director + workers) runs → Telegram Summarizer shortens → Replier sends to
  the correct channel`.
- **Why gRPC here:** the README's claim is decoupling, not throughput — the agent logic binds
  to a stable gRPC ingest contract, so a new channel is a new gateway adapter feeding the same
  API rather than a change to the swarm. This is a seam, not a performance feature. Optional TLS
  is offered on the gateway.

**The pattern that matters for Limpet:** a **normalize-at-the-edge ingest seam** decouples the
*source* of work from the *agent that does it*. The RE analog is obvious — a target arrives as a
firmware blob, an APK, a PCAP, a binary — and the value is an ingest layer that normalizes each
into one "analysis request" shape so the recon/stance topology never learns how the target was
delivered. Whether that seam needs gRPC specifically is a separate question (§5).

## 3. Claude-as-a-tool — the model is one worker's capability, not the whole agent

ClawSwarm's deepest-reasoning path is **not** the director and **not** a model wired into the
core loop. It is one worker — `ClawSwarm-Developer` — whose tool is **`run_claude_developer`**
(Claude Code for implementation and debugging). The README's framing: "Deep reasoning and code
are handled by Claude (e.g. via the Developer worker's `run_claude_developer`)."

Three structural consequences, each relevant to Limpet:

1. **Claude is a tool among many, invoked by one worker, dispatched by the director.** The
   director decides *whether this message even needs deep reasoning* before any Claude call
   happens — a greeting routes to `ClawSwarm-Response` (LLM-only, cheap) and never reaches the
   Developer worker. The expensive model is gated behind a routing decision, not the default
   path. This is a cost/latency tiering: the swarm uses a cheap general LLM for the director and
   simple workers, and reserves Claude for the one worker that needs it.
2. **Prompts are code, not config.** Agent and Claude-tool prompts are Python strings in the
   **`claw_swarm.prompts`** module; override via **`create_agent(system_prompt=...)`** or edit
   the module. Versioned with the code, diffable, no runtime prompt store. (This matches
   Limpet's own "prompts in code" posture — the REx stance agents are agent-def files under
   `REx:claude-skill/agents/`, not a database.)
3. **The model is swappable per role.** `AGENT_MODEL` is an environment variable; the director
   and simple workers can run a different model than the Developer worker's Claude. This is the
   same per-role model routing Limpet plans via LiteLLM (`design/wq3806_limpet_architecture.md`
   §6) — ClawSwarm shows the pattern working, if at a coarser grain (env var + `create_agent`,
   not a routing table).

## 4. Production-loop concerns — long-running loop, Dockerfile, env config, systemd

- **CLI:** `clawswarm run` launches the full stack (gateway + agent in one process group).
  Channel tokens and `AGENT_MODEL` are passed via `--env-file .env` or `-e`.
- **Long-running agent loop** "suitable for systemd, Docker, or managed runtimes" — the process
  is meant to stay up and serve, not run once and exit. 24/7 operation is a stated design goal.
- **Dockerfile** included, **Python 3.12**. Environment-based configuration (every token/model
  via env), optional TLS on the gRPC gateway.
- **Caveat on "health checks":** the ticket lists health checks as a dissection target, but the
  README I read does not describe a health-check endpoint, liveness probe, or readiness
  contract — only "long-running loop suitable for systemd." **Do not assume a health endpoint
  exists** without reading the source; "suitable for systemd" is an operational claim, not
  evidence of a probe. Marked as unverified.

## 5. CAVEAT — what ClawSwarm is, and what not to copy

ClawSwarm is a **consumer chatbot** (Telegram/Discord/WhatsApp) with a **crypto token-launch**
worker (`ClawSwarm-TokenLaunch`: `launch_token`/`claim_fees` on Solana). That is a different
problem than Limpet's offline, adversarial, single-operator RE loop. Copy the *topology*, not
the *application*:

- **The token-launch / Solana / Exa-search workers are domain cargo** — irrelevant to RE and
  not to be imported. The transferable unit is the director→`SwarmSpec`→worker shape and the
  roster-extension model, nothing below it.
- **gRPC + multi-channel gateway is solving a problem Limpet does not have.** Limpet ingests
  targets an operator hands it, not messages streaming from three chat platforms. The
  *normalize-at-the-edge* idea (§2) transfers; the gRPC-server-always-listening machinery is
  overhead for a loop that is dispatched per-target, not per-inbound-message. Adopt the seam
  concept, not the transport.
- **"Compiles to Rust" and "production-ready" are unverified vendor claims** at version
  **0.0.5** — a pre-1.0 package. The README asserts a Rust build path; nothing I read
  demonstrates it. Weight accordingly.
- **No adjudication stage.** ClawSwarm's director dispatches and a Summarizer compresses — there
  is **no critic/reconcile step** that tries to *falsify* a worker's output before it ships to
  the user. That is fine for a chatbot and disqualifying for RE: Limpet's whole premise is the
  propose→critic→adjudicate gate (`limpet/critic.py:12-28`). ClawSwarm is a delegation pattern
  without a verification pattern; Limpet needs both.

## 6. LIMPET VALUE — director-worker delegation for the RE topology

The ticket's thesis is correct: **director-worker delegation maps cleanly onto the multi-agent
RE topology, and gives a clean way to scale from one agent to many specialists without
rewriting the core loop.** Concretely, against `design/wq3999_rex_skill_dissection.md:131-182`:

1. **Director = `re-reconcile`'s dispatcher half, SwarmSpec = the per-stance order.** REx
   already runs a director-shaped phase: recon produces evidence, then *something* dispatches
   the five stance agents in one message (`wq3999_rex_skill_dissection.md:141-147`). ClawSwarm's
   `SwarmSpec` is the missing name for that order object — a **structured** (not free-text)
   delegation that says "run `re-arithmetic` and `re-lifecycle` against this evidence set,"
   chosen by target class. Adopt a typed order object between recon and the stances instead of
   an implicit "dispatch all five always."
2. **Roster extension without core rewrite is the headline win.** REx's stated extension path
   is "add arithmetic/lifecycle/logic by target class" (`wq3999` §4.3). ClawSwarm demonstrates
   the mechanism: a new stance is a new worker with its own prompt + tools, registered with the
   director; the dispatch loop is untouched. This is the direct answer to "scale from single
   agent to multi-specialist" — the director routes to whoever is registered, so a sixth stance
   (`re-crypto`, say) is additive.
3. **Per-role model tiering (§3) is Limpet's LiteLLM routing, prototyped.** A cheap model for
   the director's planning/routing and the simple stances, an expensive model (Claude) reserved
   for the deep stance — that is exactly the per-role routing `design/wq3806_limpet_architecture.md`
   §6 defers to LiteLLM. ClawSwarm's `AGENT_MODEL` + `create_agent(system_prompt=...)` is the
   coarse version; Limpet's routing table is the fine version of the same idea.
4. **Prompts-in-code (§3) validates Limpet's existing posture** — `claw_swarm.prompts` ≈ REx's
   agent-def files. No runtime prompt store to secure, version, or leak; prompts diff with the
   code.

**Build separately / keep from Limpet's own design (do not inherit from ClawSwarm):**

- **The adjudication gate.** ClawSwarm has none (§5). Limpet's director-worker layer must feed
  `limpet/critic.py`'s deterministic gate + a falsifying critic call before any stance output
  becomes a committed finding — the stances propose, they do not ship. ClawSwarm gives the
  *delegation* half of the loop; `limpet/critic.py` is the *verification* half ClawSwarm lacks.
- **Stance isolation.** ClawSwarm workers are independently scoped but there is no stated rule
  that one worker may not see another's output — the director recombines everything. Limpet's
  stances **must not** see each other's findings (`re-safety.md:28` per
  `wq3999_rex_skill_dissection.md:147`), or five independent reads collapse into one held five
  times. Keep that rule; it is Limpet's, not ClawSwarm's.
- **The ingest transport.** Adopt normalize-at-the-edge (§2) as a concept; do **not** adopt an
  always-listening gRPC server for a per-target dispatch loop (§5).
