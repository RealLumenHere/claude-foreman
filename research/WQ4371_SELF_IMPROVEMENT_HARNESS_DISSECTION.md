# wq#4371 — LIMPET RESEARCH: dissect self-improvement + harness patterns

Two shipping, sourced agent frameworks on opposite ends of the same question — "how much
scaffolding should an agent core carry, and when does it earn the right to carry more?" —
plus a reference pointer this ticket asked for but that turns out not to exist yet in this
repo. **AgentScope 2.0** answers it by keeping the reasoning loop frozen and bolting
capability tiers onto it from the outside (`HarnessAgent` wraps `ReActAgent`, never replaces
it). **GBase** answers it by advertising a closed self-modification loop as the headline
feature — and, on inspection of the actual source, shipping the supporting plumbing for that
loop (memory, experience distillation, manual rollback tooling) while the loop's own
dedicated module is a literal empty file. The gap between the two is the load-bearing lesson
for Limpet's own agent core (`design/wq3806_limpet_architecture.md` §5-§6, lines 233-309),
which already hand-rolls pieces of both without either's framework underneath.

**Sourcing note (instinct 8 — anti-fabrication):** both frameworks postdate the model cutoff
(Jan 2026; today is 2026-10-19), so every claim below came from `fleet-search
web`/`read`/`reddit`/`hn` plus direct `curl` against `raw.githubusercontent.com` and
`api.github.com` on 2026-10-19 (SearXNG's brave/duckduckgo/startpage engines were
suspended/timed-out/CAPTCHA'd this run throughout; google-cse carried web discovery).
Primary sources fetched directly: `docs.agentscope.io/en/versions/2.0.2` (AgentScope 2.0
overview), `java.agentscope.io/v2/en/docs/building-blocks/middleware` (full middleware doc,
fetched via `fleet-search read`), `github.com/agentscope-ai/agentscope-java/releases` (the
release notes introducing `HarnessAgent`); for GBase, the canonical repo's own
`README.md`, `lib/VERSION.md`, and `tools/rollback.py` (fetched via
`raw.githubusercontent.com/garyqlin/gbase/main/...`), plus `api.github.com/repos/garyqlin/
gbase/contents/lib/evolution_engine.py` and four sibling-module size checks (confirming
which modules are real vs. stub) — all against the canonical, non-forked repo identified
below. For Dify/Sim: `dify.ai`, `github.com/langgenius/dify`, `sim.ai/blog/v0-5`,
`docs.sim.ai/introduction`, `github.com/simstudioai/sim`. Nothing below is extrapolated from
parametric knowledge — every quoted phrase was fetched this run.

---

## 1. AgentScope 2.0 — opt-in harness on an unchanged ReAct kernel

**What it is:** AgentScope is Alibaba's open-source multi-agent framework
(`arxiv.org/abs/2402.14034`, 2024 origin paper; `agentscope.io` current product site). 2.0
is "a breaking change from 1.0, with significant improvements in the core abstractions,
APIs and architecture" (`docs.agentscope.io/en/versions/2.0.2`). The ticket's "ReAct kernel"
and "opt-in harness" framing matches AgentScope Java's own release-notes language for its
newest layer, almost verbatim:

> *"HarnessAgent: Extends ReActAgent through Middleware and Toolkit channels, adding
> workspace, memory, sandbox, subagents, skills, and plan mode as engineering
> infrastructure — the core reasoning loop is preserved, only augmented."*
> (`github.com/agentscope-ai/agentscope-java/releases`)

- **Composition, not replacement.** `HarnessAgent` is explicitly a decorator over
  `ReActAgent`, not a parallel runtime: every capability bullet in that quote — workspace,
  memory, sandbox, subagents, skills, plan mode — arrives through two channels
  (`Middleware`, which injects logic at six lifecycle hook points, and `Toolkit`, which
  governs tool registration) rather than through a rewritten core loop. A `HarnessAgent`
  instance is "fully stateless — all per-call mutable state is propagated via Reactor
  Context, allowing a single instance to safely serve multiple (userId, sessionId)
  combinations concurrently" (same release notes) — so adding capability tiers does not
  cost per-session agent instances.
- **Middleware is opt-in per instance, not auto-attached.** `java.agentscope.io/v2/en/docs/
  building-blocks/middleware`: six hook points (`onion` — wraps next.apply(input) and can
  observe the intermediate stream; `transformer` — pipeline, previous output feeds next
  input; `notification` — one-way, no delegation), and "AgentScope packs a set of hooks
  into a single `MiddlewareBase` implementation... Pass the instances to the builder's
  `middlewares(...)`." Nothing is wired by default; every capability — including tracing —
  is a middleware instance the integrator explicitly constructs and registers.
- **`OtelTracingMiddleware` — built-in, but still opt-in; its *default* is about which SDK
  it reads, not whether it runs.** The ticket's "default OTel instrumentation" needs one
  correction: the middleware class ships in the framework and needs no custom code to use,
  but it is not auto-attached to a bare `ReActAgent` — the integrator still calls
  `middlewares(new OtelTracingMiddleware())`. Once constructed, it "instruments `onAgent`,
  `onModelCall`, `onActing`, producing nested spans: `invoke_agent <name>` (wraps a full
  reply), `chat <model>` (wraps each model API call), `execute_tool <name>` (wraps each tool
  execution)," and *that* behavior is zero-config by default — "by default,
  `OtelTracingMiddleware` reads the process-wide `GlobalOpenTelemetry` instance," with no
  SDK registered it's a documented no-op rather than an error. A side effect worth knowing
  before adopting it: constructing any instance registers a JVM-wide Reactor hook
  (`ContextPropagationOperator.registerOnEachOperator()`) the first time, so span context
  survives `publishOn`/`subscribeOn` hops anywhere in the process — but only for publishers
  assembled *after* that first construction.
- **Workspace System — the capability closest to a maturity dial.** `docs.agentscope.io`'s
  own framing: "Move an agent from your laptop to a Docker host or an E2B sandbox by
  changing one line, with working directory, MCP clients and skills cleanly isolated per
  user, agent or session." This is the one AgentScope 2.0 feature that is literally a dial
  from zero isolation to full remote-sandbox isolation behind a single config line, which is
  the clearest match for the ticket's "start lightweight, selectively enable... as the
  processor matures" framing.
- **Execution Security and Human-in-the-loop are the other two named 2.0 pillars**, both
  gate-shaped rather than tier-shaped: "dangerous tool calls can be denied or held for
  review, and untrusted code can run inside a sandbox" (Execution Security), and "users can
  confirm or edit tool arguments mid-run... with the agent resuming exactly where it paused"
  (Human-in-the-loop, delegable to the integrator's own backend instead of executed
  in-process).

---

## 2. GBase — recursive self-improvement, advertised loop vs. wired loop

**What it is, and the one fact that matters before trusting any of its marketing copy:**
GBase ("Genius Base") is a real, MIT-licensed, single-author open-source agent framework —
canonical repo `github.com/garyqlin/gbase`, currently v0.6.1, by Gary Lin (README byline,
2026). It is **not** an organically-starred project in the normal sense: the GitHub topic
pages for `mirror-memory` and `cognitive-architecture` list 22 repositories (per each topic
page's own "Here are 22 public repositories matching this topic" header) carrying the
*identical* README description ("GBase — Recursive Self-Improvement Agent Framework.
Memory, evolution, quality gates, identity system, and 40+ auto-registered tools.") under
personal-account names with template-farm-shaped suffixes (`aortiz05368`, `Chalermchai913`,
`ElenaMehta89009`, `Claudiar27119`, confirmed via `curl`'d raw HTML `href="/<owner>/gbase"`
links) — i.e. this is a widely-cloned personal template, not a project with 22 independent
adopters each customizing it. The canonical origin is identifiable only because one fork's
README retains the original release badge linking to `github.com/garyqlin/gbase/releases/
tag/v0.6.1` and the author's own closing attribution line, `@garyqlin` / `📦 GBase`.

- **The headline RSI claim, verbatim from the canonical README's feature table:**
  > *"🔄 **Recursive Self-Improvement (RSI)** | A full-evolution engine: stability audit,
  > performance evaluation, rollback decision, and automatic recovery. Call
  > `full_evolution_cycle()` to trigger — your agent analyzes its own outputs, detects
  > failure patterns, and rewrites itself for the next round"*

  This is an exact match for the ticket's framing — which is itself informative: the ticket
  describes GBase's marketing copy accurately, which made the next step (checking whether
  the copy describes working code) the actual research contribution here.

- **What is real and substantial, confirmed by file size via
  `api.github.com/repos/garyqlin/gbase/contents/lib/<file>`:**
  - `lib/mirror.py` (63,482 bytes) — **Mirror Memory**, the README's "long-term memory with
    active recall." `lib/VERSION.md`'s own chassis table (Chinese-language internal
    versioning doc, "底盘" = chassis / stable core) describes it as "鉴面引擎（三层记忆
    hot/warm/auto）" — a three-tier (hot/warm/auto) memory engine, marked `✅ 稳定`
    (stable) alongside the other 15 chassis files that may only receive P0 fixes.
  - `lib/experience.py` (25,959 bytes) — the **Experience Engine**: "every interaction is
    distilled into reusable knowledge — not raw logs, but structured patterns your agent
    can query" (README feature table). `VERSION.md` glosses it as "经验引擎（提取/沉淀/
    检索）" — extraction/settling/retrieval — also chassis-tier stable.
  - `lib/lifeline.py` (14,627 bytes) — **Lifeline**: "automatic git pre-snapshots before
    every code modification. 'Undo' is always one command away" (README). This is the
    actual rollback *substrate* — a snapshot taken before every write, independent of
    whether anything ever decides to use it.
  - `tools/rollback.py` (72 lines, read in full) — a **manually LLM-invoked** tool
    surface over that substrate: `rollback_list`, `rollback_restore`, `rollback_cleanup`,
    `rollback_stats`, backed by `lib/backup.py`. Its own module docstring calls it "波段一：
    刹车装置" — "Wave 1: braking device" — i.e. the author's own naming marks this as a
    first-wave safety mechanism the agent calls deliberately, not a step inside an
    autonomous audit→decide→recover loop.

- **What is advertised but not wired, confirmed the same way:** `lib/evolution_engine.py` —
  the module whose name is the obvious home for `full_evolution_cycle()`'s "stability
  audit, performance evaluation, rollback decision, automatic recovery" pipeline — resolves
  via `api.github.com/repos/garyqlin/gbase/contents/lib/evolution_engine.py` to **`"size":
  0`**, with git blob SHA `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391` — the canonical SHA of
  a completely empty file (`git hash-object /dev/null`). It is also absent from
  `VERSION.md`'s 16-file chassis table entirely. A grep of every other inspected module for
  `full_evolution_cycle`/`stability_audit`/`rollback_decision` — `lib/kernel.py` (the core
  run loop), `main.py` (CLI/HTTP/WebSocket entry point), `lib/sleep_cycle.py` (the "offline
  consolidation" module, the next most plausible caller) — found zero references. On the
  evidence available from the canonical repo's current `main` branch, **the closed
  self-improvement loop GBase's README leads with does not exist as working code**; what
  exists is the three real memory/experience/snapshot primitives a *real* evolution engine
  would need, plus a manually-triggered rollback tool, with the orchestrating module left
  as an empty stub carrying the right name.

- **The lesson this is worth to Limpet is not "GBase is bad" — forks update the file
  independently and the author may ship it later — it is "never credit an advertised
  closed-loop capability until you have checked that the loop's own code exists and is
  called."** That check cost four `curl`/API calls here. Skipping it would have produced a
  dissection that recommended studying "GBase's rollback-decision algorithm" when no such
  algorithm is in the fetched source.

---

## 3. Dify / Sim — a reference pointer the ticket names but this repo hasn't dissected yet

The ticket states this reference pattern is "already covered in prior tickets." That claim
does not hold up against this repo's own history: `grep -rn -i "dify\|Sim Studio\|workflow-
as-mcp" research/ design/` across every WQ43xx/WQ44xx dissection (`WQ4356`, `WQ4357`,
`WQ4358`, `WQ4361`, `WQ4362`, `WQ4364`, `WQ4367`, `WQ4368`, `WQ4369`, `WQ4370`) returns zero
substantive hits — one false-positive grep match on "modify," and a single passing mention
of Dify as a container in `research/FLEET_SERVICE_ATLAS.md:86`'s inventory list (Open WebUI,
LibreChat, Dify named as chat/agent-interface containers already running on the fleet, no
architectural discussion). Recording this honestly rather than citing a dissection that does
not exist (ISSUE-LOG-worthy if a future ticket is dispatched believing this is covered):

- **Dify** (`dify.ai`, `github.com/langgenius/dify`) is real and current: "the platform for
  production-ready agentic workflows. Build agents, knowledge pipelines, models, and tools
  on one canvas, deployable on Cloud, VPC, or self-hosted." Its workflow product
  (`dify.ai/workflows`) is a collaborative visual canvas combining model calls, knowledge
  retrieval, tools, and code — the "visual workflows" the ticket names, confirmed, not
  dissected further here since that is out of this ticket's scope (reference-only per the
  ticket body).
- **Sim** (`simstudioai/sim`, formerly "Sim Studio") is real and current, and the ticket's
  "workflow-as-MCP-server" framing is Sim's own literal product description, not a
  paraphrase: `sim.ai/blog/v0-5` answers the question directly — *"Yes. Any workflow can be
  deployed as an MCP server, and it becomes a callable tool for MCP-compatible agents like
  Claude Desktop or Cursor."* `docs.sim.ai/introduction` confirms the same deploy surface
  ("Deploy a workflow to make it available through an API, a chat interface, or an MCP
  server").

If a future ticket wants an actual dissection of either against Limpet's shape (Limpet has
no visual-canvas user and no workflow-export need today — it is a single operator-driven
investigation loop, §6), it should be dispatched as new research, not assumed covered.

---

## Evaluation: both patterns against Limpet's actual agent core

Limpet's agent core (`design/wq3806_limpet_architecture.md` §5-§6, lines 233-309) is a
hand-rolled stack with no harness layer and no self-modification loop underneath it:
`limpet/obligations.py` (an unanswered-question ledger, hard-gated — `obligations verify`
exits 3 and refuses "done" with any item open), `limpet/critic.py` (a propose/critic/
adjudicate pass before a finding commits), and per-workspace containment (§5, lines 233-257:
one workspace per target, container/namespace isolation, no network by default, git-backed
so every step is diffable and exportable).

- **AgentScope's Workspace System vs. Limpet's §5 containment — convergent design, already
  solved independently.** AgentScope's "laptop → Docker host → E2B sandbox by changing one
  line" dial and Limpet's `/var/limpet/workspaces/<target-id>/` (container-bound,
  no-network-by-default, git-committed) are the same idea arrived at separately: per-session
  isolation that scales from local to remote without changing the agent's reasoning loop.
  **Do not adopt AgentScope's Workspace System** — Limpet's version already fits its own
  threat model (untrusted firmware/APK artifacts, §0's "a planted `json.py` must not run on
  `import json`" rule) more specifically than a generic Docker/E2B swap would.
- **AgentScope's "sandbox" and Limpet's "sandbox" are different concepts wearing the same
  word — do not conflate them when scoping future work.** AgentScope's sandbox gates
  *untrusted code execution*. Limpet's existing sandbox references
  (`limpet/limpet_tools.py:21`, `limpet/registry_trust.py:159,254`) gate *untrusted plugins*
  by trust tier (§3) — a different axis (who wrote this tool vs. what this code is allowed
  to touch at runtime). Limpet has no AgentScope-shaped execution sandbox today; whether it
  needs one is a question about firmware-emulation containment, not about the agent core,
  and belongs in a Limpet-specific ticket, not a wholesale AgentScope adoption.
- **Subagent fan-out: skip, same conclusion as every prior dissection that checked it.**
  AgentScope's `HarnessAgent` lists `subagents` as a composed capability; Limpet is one
  investigation per workspace session by design (§5), matching the independent conclusions
  already on record in `research/WQ4367_ORCHESTRATION_DISSECTION.md` (OpenAI Symphony) and
  `research/WQ4368_ENTERPRISE_META_FRAMEWORKS_DISSECTION.md` (MAF's background agents,
  flagged experimental even there). Three separate frameworks' fan-out features now line up
  against the same "no multi-ticket backlog inside one Limpet session" fact; the answer
  doesn't change on a fourth look.
- **OTel-style tracing: a real gap, not yet a priority.** Limpet's agent core has no
  span-level instrumentation of the identify→orchestrate→verify→report loop today — nothing
  in `design/wq3806_limpet_architecture.md` §6 or `limpet/*.py` names tracing. AgentScope's
  `OtelTracingMiddleware` pattern (three span types: invoke_agent / chat / execute_tool,
  attached only when explicitly constructed) is the right *shape* to borrow once Limpet's
  tool-orchestration steps get complex enough that latency/cost visibility across a session
  matters more than reading the workspace's own git-committed step log — not before, since
  the git-commit-per-step trail (§5) already gives replayable, auditable history without a
  tracing dependency.
- **GBase's cautionary lesson is the one to carry forward structurally, not its RSI code.**
  Limpet's `obligations.py`/`critic.py` gates are the opposite of GBase's dormant
  `evolution_engine.py`: both are hard-enforced with a nonzero exit code
  (`obligations verify` → exit 3; critic's adjudicate step records a ledger obligation for
  any contested finding rather than silently dropping it) and both are actually called from
  the investigation loop today. **There is no self-improvement-loop gap to close here** —
  Limpet never advertised an autonomous audit→rollback cycle it hasn't built, and should
  resist adding GBase-style "evolution" framing (`full_evolution_cycle()`-shaped marketing)
  to any future capability unless the implementing module is as real as `obligations.py`
  already is.

## Migration path recommendation — start bare, add capabilities incrementally

For Limpet's agent core specifically, in the order each tier earns its cost:

1. **Tier 0 (today, keep as-is):** bare ReAct-shaped loop (identify → choose toolset →
   orchestrate → verify → report, §6) plus the two hard gates it already has —
   `obligations.py`'s ledger and `critic.py`'s propose/critic/adjudicate pass. Both already
   outperform every opt-in equivalent inspected across this and prior dissections (MAF's
   `TodoProvider` has no verify-step enforcement per `research/
   WQ4368_ENTERPRISE_META_FRAMEWORKS_DISSECTION.md`; GBase's evolution loop isn't wired at
   all). Nothing to add at this tier — only to protect from downgrade.
2. **Tier 1 (next, genuine gap):** a tool-surface-enforced plan/execute mode, per
   `WQ4368`'s already-landed recommendation (`research/
   WQ4368_ENTERPRISE_META_FRAMEWORKS_DISSECTION.md:241-252`) — reuse that conclusion rather
   than re-deriving it; this dissection's AgentScope findings don't change it, since
   AgentScope's `HarnessAgent` plan mode is the same "declared-state-the-harness-enforces"
   shape MAF's `AgentModeProvider` already was.
3. **Tier 2 (add once orchestration complexity earns it):** span-level tracing across
   model-call/tool-call/agent-reply boundaries, AgentScope-`OtelTracingMiddleware`-shaped
   (explicitly constructed, not auto-attached, cheap no-op when no SDK is registered).
   Trigger condition: when a session's git-committed step log (§5) stops being enough to
   answer "where did the time/cost go," not on a calendar.
4. **Tier 3 (deliberately skip unless a concrete need appears):** subagent fan-out and any
   AgentScope/MAF-style execution-sandbox swap — Limpet's one-workspace-per-session model
   and §5 containment already cover the problems those features solve elsewhere, and three
   independent dissections (this one, WQ4367, WQ4368) now agree fan-out has no Limpet use
   case.
5. **Standing rule for any future "self-improvement" or "evolution" capability proposed for
   Limpet:** before crediting the capability in a design doc, confirm the implementing
   module is non-empty and actually called from the loop — the GBase check above
   (`api.github.com/repos/garyqlin/gbase/contents/lib/evolution_engine.py` → `"size": 0`)
   is the exact shape of verification that would have caught this before it reached a
   recommendation, and the same check costs under five tool calls against any candidate
   repo.
