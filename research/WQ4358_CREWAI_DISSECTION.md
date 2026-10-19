# wq#4358 — Dissection: CrewAI role-based multi-agent framework for Limpet build-vs-buy

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Source:** CrewAI's own published docs
(`docs.crewai.com`, multiple dated snapshots retrieved 2026-10-19 via `fleet-search web`;
SearXNG general engines suspended/CAPTCHA'd, DeepSeek Search fallback used per the tool's own
`note` field — ISSUE-LOG precedent, same outage pattern as wq#4347/wq#4356), the project's PyPI
listing (`pypi.org/project/crewai/1.15.26`), GitHub issues/PRs on `crewAIInc/crewAI`, and a
third-party structured framework review (`EdwardAThomson/agent-reviews`,
`reviews/frameworks/crewai.md` + `data/agents/crewai.yml`) that independently corroborates the
license/dependency/tool-count claims below. **Scope honesty:** this is a documentation and
community-source dissection, not a read of `crewAIInc/crewAI`'s source tree — `WebFetch`/git
clone of the repo is not on this worker's allowlist. Code-level claims below (e.g. `Crew.
_run_hierarchical_process`, `BaseAgentTool._execute`'s coworker-matching logic) are quoted from
a third party's own source-reading writeup (`doc.holiday/library/crewai/the-hierarchical-process`),
not independently verified against the file; everything else is the vendor's own documented
behavior, which may be aspirational vendor framing in places (see §7 CAVEAT).

Companion to the sibling LIMPET orchestration dissections — `research/WQ4347_CLAWSWARM_DISSECTION.md`
(director→`SwarmSpec`→worker delegation topology) and `research/WQ4356_A2A_PROTOCOL_DISSECTION.md`
(the wire contract for cross-agent delegation). Where those two examined a narrow chatbot
framework and a transport protocol, CrewAI is examined here specifically as a **build-vs-buy
candidate**: a complete, MIT-licensed, batteries-included implementation of the same
director/worker-delegation shape, with a second orchestration primitive (Flows) layered on top.
Grounds against the planned recon→stances→reconcile topology in
`design/wq3999_rex_skill_dissection.md:131-182` and the deterministic adjudication gate in
`limpet/critic.py:12-35,81`. Limpet's agent core and LiteLLM client are still design-only
(`limpet/model_router.py:15-22`, `design/wq3806_limpet_architecture.md` §6), so this is a
pre-build decision input, not a retrofit onto running code.

---

## 1. Role/goal/backstory agent model — mapped onto the 5-hunter topology

CrewAI's base unit is an `Agent(role=..., goal=..., backstory=..., tools=[...], allow_delegation=...)`.
Three free-text/structured fields plus a tool grant are the entire agent definition — no
separate planner/executor split at the framework level; the LLM receives role+goal+backstory as
system-prompt framing and the tool list as its action space.

Direct mapping to REx's five stance agents (`design/wq3999_rex_skill_dissection.md:141-147`):
each of `re-bughunt`, `re-safety`, `re-arithmetic`, `re-lifecycle`, `re-logic` is already a
role+goal pair with a distinct prompt file — CrewAI's `Agent` constructor is a direct container
for exactly that shape, and `tools=[...]` is the per-stance tool grant Limpet's isolation rule
already requires (stances read the shared `evidence/` tree and nothing else,
`wq3999_rex_skill_dissection.md:172-174`). Nothing here is novel relative to Limpet's own
design; CrewAI's contribution is that the role/goal/backstory/tools shape is **already wired to
an executor loop, a prompt template, and a delegation mechanism (§3)** — the parts Limpet's
design still has to build.

`re-recon` maps to CrewAI's **manager agent** in `Process.hierarchical` (§4): a synthesized or
custom `Agent` that receives `AgentTools(agents=self.agents).tools()` — i.e. the crew's own
worker agents turned into callable tools — and is the only agent CrewAI lets delegate by
default in hierarchical mode. CrewAI's rule that a manager agent **must not carry domain
tools** (`crew.py` raises `Exception("Manager agent should not have tools")` if a supplied
manager already has any, per the third-party source-reading review) is a documented
enforcement of the same "recon never concludes, never does a stance's job" boundary
`re-recon.md:16-18` states as prose and Limpet's design has not yet encoded as a check.

## 2. Crews + Flows duality — Crew for hunter collaboration, Flow for reconcile pipeline

**Crew** = autonomous collaboration. A `Crew(agents=[...], tasks=[...])` hands a task list to a
process (§4) and the agents work it with the framework's own delegation tools available; control
flow is emergent, not authored. **Flow** = a plain Python class with `@start()`/`@listen()`/
`@router()`-decorated methods over a typed Pydantic `state`; branching is a method that returns
a string, looping is a method that calls itself, fan-out/fan-in is `and_()`/`or_()` composing
listener conditions. CrewAI's own framing of when to use which (`AI-Research-SKILLs` reference
doc, fetched 2026-10-19): Crews for "simple multi-agent collaboration" and open-ended
collaboration, Flows for "conditional branching," "complex state management," and
"event-driven workflows" — and explicitly, Flows can **host** Crews ("hybrid" row, checked for
Flow only): a Flow step can be a few lines of deterministic Python, or it can be
`crew.kickoff()` spinning up a full autonomous team, inside the same typed-state pipeline.

Mapped onto Limpet's loop (`wq3999_rex_skill_dissection.md:131-182`):
- **Crew** for Phase 2 — the five stance agents reading the same `evidence/` tree, each an
  `Agent` with its own role/goal/tools, non-communicating by construction (no `allow_delegation`
  granted between stances, since isolation is the point, not collaboration).
- **Flow** for the recon(1) → stances(N, parallel) → reconcile(1) **spine** — `@start()` runs
  recon and writes `self.state.threat_model`/`ruled_out`, a fan-out `@listen` set (or `and_()`
  over N stance-crew kickoffs) runs the stances against that fixed state, and `@listen(and_(...))`
  on reconcile reads all N outputs plus the code before adjudicating. This is a precise match
  for the CrewAI vendor's own stated rationale for Flows over Crews: Limpet's phase order,
  "recon never concludes" gate, and reconcile's five-rule adjudication (all-agree /
  same-sink-opposite / different-sinks / alone / nothing,
  `wq3999_rex_skill_dissection.md:148-154`) are **deterministic control flow with LLM-heavy
  nodes**, exactly the shape a third-party 2026 field guide (`callsphere.ai`) describes as "the
  2026 production answer": "Flows as the spine, Crews as the muscle." A pure Crew (manager
  decides who runs when) would re-introduce exactly the risk REx's topology exists to prevent —
  an LLM manager improvising whether a stance runs or sees another stance's output, instead of
  the fixed, blind, parallel dispatch the doctrine requires.

## 3. DelegateWork and AskQuestion — built-in agent-to-agent primitives

When `allow_delegation=True`, CrewAI auto-grants an agent two tools (confirmed independently by
CrewAI's own `concepts/collaboration` doc and `EdwardAThomson/agent-reviews/crewai.md`:
"Delegation tools (DelegateWork, AskQuestion) enable agent-to-agent communication"):

- **`Delegate work to coworker(task, context, coworker)`** — assign a bounded task to a named
  teammate; the tool's own prompt text is explicit that the coworker "know[s] nothing about the
  task" so the delegator must pass "ALL necessary context," not a reference (GitHub issue #448).
- **`Ask question to coworker(question, context, coworker)`** — a lighter-weight, read-only
  variant: gather information from a colleague without handing them an executable task.

Mechanically (per the source-reading field-guide, `doc.holiday/library/crewai/the-hierarchical-process`):
both inherit from a shared `BaseAgentTool`; `_execute` resolves `coworker` by **exact-match on
the normalized role string** (whitespace collapsed, quotes stripped, casefolded) — "tolerant
name matching... does not guess from meaning or infer a best match." The tool then builds a
`Task` for the resolved agent and calls `selected_agent.execute_task(...)` **synchronously
inside the tool call**, so the delegator's turn blocks until the coworker's answer returns as
the tool's observation.

**Why this is exactly recon-to-hunter communication, with a caveat.** The two-tool split
(assign work vs. ask a question) is the right primitive shape for recon handing stances their
threat model and for reconcile pulling clarification from a stance about an ambiguous finding.
But CrewAI's delegation is **synchronous, point-to-point, and bidirectional by permission**: any
agent with `allow_delegation=True` can call any named coworker, and a GitHub-reported production
failure mode (`crewAIInc/crewAI` issue #448 comments) is agents "delegating back and forth
indefinitely" without a hard stop. Limpet's stance isolation rule is the opposite shape — stances
must categorically **not** see each other's output (`re-safety.md:28` per
`wq3999_rex_skill_dissection.md:147`) — so adopting CrewAI's delegation tools verbatim for the
stance layer would need `allow_delegation=False` hard-set on every stance (which the framework
supports and defaults to since a cited behavior change: "Delegation is now disabled by default to
give users explicit control"). The primitive is adoptable; the default posture needs the same
explicit lockdown REx's prose already specifies.

## 4. Sequential and hierarchical process modes

`Process.sequential` (the default) walks the task list in authored order, one agent per task as
assigned. `Process.hierarchical` requires a `manager_llm` or a custom `manager_agent`; CrewAI
auto-synthesizes a manager (role/goal/backstory pulled from a localized prompt file,
`translations/en.json`) if none is supplied, grants it `allow_delegation=True` unconditionally,
and wires every other agent into it as a callable tool
(`AgentTools(agents=self.agents).tools()`). Per the source-reading field guide, task **order is
unchanged** between the two modes — `_run_hierarchical_process` and `_run_sequential_process`
both call `_execute_tasks(self.tasks)`; the only difference is which agent executes each task
turn (`_get_agent_to_use` returns the fixed assignee in sequential, always the manager in
hierarchical). So "hierarchical" is not a scheduler change, it is an **executor substitution** —
every task runs through the manager, who may re-delegate it.

**For Limpet:** recon-as-hierarchical-manager (ticket's framing) is a reasonable label but an
imprecise mechanical match. CrewAI's hierarchical manager delegates **within a single task
turn**, re-planning per task; Limpet's recon phase runs once, completely, before any stance
starts, and never re-enters the loop once stances are dispatched
(`wq3999_rex_skill_dissection.md:135-140`: "Everything downstream reads its output"). Limpet's
shape is closer to CrewAI's **Flow with recon as `@start()`, stances as a Crew kicked off by a
`@listen`** (§2) than to `Process.hierarchical` — the hierarchical process's selling point
(a manager deciding mid-run whether to delegate) is the discretion Limpet's design deliberately
removes from recon.

## 5. Automatic task delegation and output passing

Within a `Crew`, `Task.context` lets one task's output flow as input to another explicitly, and
tasks without an explicit `agent=` fall through to the hierarchical manager for routing. Output
passing between crew members is otherwise manual — the delegation tools (§3) are the only
*automatic* cross-agent data path; nothing make CrewAI auto-aggregate five parallel agents'
outputs into one reconciler's input the way Limpet's reconcile phase needs. That
union/concatenation step is `Flow` state assembly (`self.state.stance_n_output = ...` per
listener, read together by the reconcile step's `@listen(and_(...))`), not a CrewAI-native
"automatic delegation" feature — the ticket's framing overstates what ships out of the box here;
it is buildable on Flow state, not a free primitive.

## 6. 75 pre-built tools

Corroborated by two independent sources (CrewAI's own PyPI listing's "Built-in memory,
knowledge, and 75 tools" framing, and the third-party review's structured YAML:
`data/agents/crewai.yml`: "75 pre-built tools spanning web search, scraping, DBs, cloud,
automation... Tools define name/description/args_schema/env_vars"), shipped as the optional
`crewai-tools` package (`pip install crewai[tools]`), separate from the MIT core. **Caveat for
RE use:** the catalog is a generic agent-productivity toolbelt (web search, scraping, DB
connectors, cloud automation) aimed at content/ops/research agents, not reverse-engineering —
none of the 75 are RE-specific (disassemblers, emulators, fuzzers). Limpet's own tool catalog
(`limpet/tool-catalog.json`, governed by `limpet/license_policy.py`'s `license_risk`/
`redistribution_posture` fields) would gain nothing RE-relevant by importing this catalog; the
value, if any, is the **tool-definition contract** (name/description/args_schema/env_vars as a
declarative shape a plugin author fills in), not the 75 tools themselves.

## 7. Unified memory with LLM-inferred importance

CrewAI's `Memory` class (introduced as "New Unified Memory System,"
`crewAIInc/crewAI` PR #4420) replaces separate short-term/long-term/entity/external memory types
with one API. On `remember()`, an LLM call infers `scope`, `categories`, and `importance` (0-1)
for the content if the caller doesn't supply them; on `recall()`, results rank by a documented
**composite score**:

```
composite = semantic_weight * similarity + recency_weight * decay + importance_weight * importance
```

where `similarity = 1 / (1 + distance)` from the vector index, `decay = 0.5^(age_days /
half_life_days)` (exponential), and `importance` is the LLM-assigned 0-1 score set at encoding
time. **Default weights, confirmed by a Chinese-language technical blog quoting CrewAI's own
"Configuration Reference" table** (`blog.gitcode.com`, cross-checked against the English
`docs.crewai.com/.../concepts/memory` composite-scoring section, which states the formula but
not every default inline): `recency_weight=0.3`, `semantic_weight=0.5`, `importance_weight=0.2`,
`recency_half_life_days=30` — this is an exact match to the ticket's "30pct recency 30-day
half-life, 50pct semantic, 20pct importance," so that line of the ticket is **verified, not
vendor-marketing-on-faith**. Memories are also organized in a hierarchical scope tree
(filesystem-like paths, e.g. `/project/alpha/decisions`) with a separate `MemorySlice`
abstraction for reading across non-contiguous scopes without granting write access to all of
them — a read-many/write-narrow pattern directly relevant to the stance-isolation problem (§3):
a reconcile-only `MemorySlice` over all five stances' scopes, while each stance's own `Memory`
handle is scoped to write only its own branch, would be a mechanical way to implement "reconcile
reads everyone, stances read no one" if Limpet ever adopts a persistent cross-session memory
layer. Today it has none (`design/wq3806_limpet_architecture.md` describes none), so this is
future-relevant, not immediately actionable.

## 8. MIT license, zero LangChain dependency, LiteLLM-compatible — with a live caveat

**MIT core, confirmed three ways:** CrewAI's PyPI page states "CrewAI is released under the MIT
License"; the third-party review's structured data confirms `license: MIT`; and the vendor's
own FAQ states "CrewAI is built entirely from the ground up, with no dependencies on LangChain or
other agent frameworks." The commercial layer (CrewAI AMP — hosted control plane, SSO/RBAC,
marketplace) is a separate paid product sitting *on top of* the MIT core, per the third-party
review: "The open-source framework remains free under MIT license and is entirely independent of
the paid AMP platform." This is a clean build-vs-buy signal — the free tier is not a crippled
trial of the paid one.

**LiteLLM compatibility is real but has shifted to optional-fallback, and LiteLLM itself just had
a supply-chain incident.** CrewAI's own `learn/litellm-removal-guide` (fetched at multiple
`docs.crewai.com` version snapshots, so not a single stale page) states plainly: *"The litellm
package was quarantined on PyPI due to a security/reliability incident. If you rely on
LiteLLM-dependent providers, you should migrate to native integrations."* CrewAI now ships native
SDK integrations for OpenAI/Anthropic/Gemini/Azure/Bedrock that need **no** LiteLLM, and routes
only the long tail (Ollama, Groq, Together AI, Mistral, Cohere, HuggingFace, and ~100 other
providers) through LiteLLM as a fallback layer. For Limpet specifically: the planned route is a
**fleet LiteLLM gateway** (`limpet/model_router.py:15-22`; `design/wq3806_limpet_architecture.md`
§6: "allow routing hard steps to a fleet model via LiteLLM"), i.e. Limpet's own LiteLLM usage is
a single trusted internal hop to a gateway Lumen operates (`design/wq3755_research_pipeline_v2.md:76`:
"the `litellm` container on Lumen"), not an install of the `litellm` PyPI package inside the
agent process. The quarantine incident is a reason to **not** `pip install litellm` directly into
Limpet's agent core (whether via CrewAI's fallback path or a hand-rolled client) without checking
its current PyPI status first — a live, recorded caveat this dissection should not let the
ticket's "LiteLLM compatible" framing paper over.

## 9. CAVEAT — vendor framing, trending complexity, verify before build

- **"Lean, lightning-fast, zero LangChain"** is accurate on the dependency claim (confirmed
  three ways, §8) but is marketing shorthand on complexity: the same third-party review notes
  "the LLM provider adapter system has a lot of indirection (`BaseLLM → LLM → provider-specific
  classes → LiteLLM fallback`)" and that the project is "**141k LOC** monorepo" — not a small
  surface to vendor wholesale. The review's own verdict: "the architectural direction is trending
  toward feature accretion rather than decomposition," and explicitly trades away "graph
  semantics, checkpointing, time-travel debugging" that LangGraph has — a second data point
  (independent of this doc's own read) for the ticket's stated alternative ("if maximum control
  is needed, take its vocabulary and implement on LangGraph").
- **Delegation can loop.** §3's "agents delegating back and forth indefinitely" is a
  user-reported production failure mode on GitHub, not a hypothetical; any adoption of CrewAI's
  delegation tools for Limpet needs an explicit `allow_delegation=False` default and a hard
  iteration cap, not trust in the framework's own guardrails (CrewAI's fix was changing a
  default, not adding a loop breaker).
- **No RE-specific tooling.** §6 — the 75 tools and the memory system (§7) are general-agent
  infrastructure; adopting CrewAI buys orchestration plumbing, not RE capability. Every stance
  agent's actual tool (decompiler driver, reachability oracle, emulator harness) is still
  Limpet's to build regardless of which orchestration framework hosts it.
- **No adjudication/falsification stage**, same gap as ClawSwarm
  (`research/WQ4347_CLAWSWARM_DISSECTION.md` §5): CrewAI's Crew and Flow both give you
  delegation and control flow, not a propose→critic→adjudicate gate. `limpet/critic.py:12-35`'s
  deterministic structural gate (`critic.py:81`, `deterministic_check`) and the five-rule
  reconcile adjudication (`wq3999_rex_skill_dissection.md:148-154`) are Limpet's to keep
  regardless of build-vs-buy — no framework examined in this research series ships that half of
  the loop.
- **Hierarchical ≠ Limpet's recon.** §4 — do not reach for `Process.hierarchical` as a literal
  implementation of "recon dispatches hunters." It re-plans per task turn; Limpet's recon phase
  runs to completion once and never re-enters. Flow + Crew-as-a-step (§2) is the closer fit.

## 10. LIMPET VALUE — the build-vs-buy decision

**Recommendation: buy the topology vocabulary and the delegation primitive shape; do not vendor
the framework wholesale onto the critical path.** Concretely:

1. **If a quick, low-friction starting point is wanted:** CrewAI's `Flow` (spine) +
   `Crew` (per-phase muscle) is, among everything this research series has examined
   (ClawSwarm's `HierarchicalSwarm`, Google A2A's wire protocol), the **most literal structural
   match** to recon(1)→stances(N,blind,parallel)→reconcile(1): a `@start()` recon step, a
   `Crew` of five non-delegating stance `Agent`s kicked off from a fan-out listener (or five
   independent `Crew.kickoff()` calls under `and_()`), and a `@listen(and_(...))` reconcile step
   that reads all five plus the code. MIT license (§8, confirmed three ways) means no licensing
   blocker to actually running it. This is **lower friction than building a bespoke orchestrator
   from scratch** if Limpet's agent core needs to ship soon and the team is willing to accept
   CrewAI's dependency surface and its 141k-LOC blast radius (§9) in exchange.
2. **If maximum control is the priority — the stronger recommendation given Limpet's
   constraints:** take CrewAI's **vocabulary**, not its runtime, and implement on LangGraph (the
   ticket's own stated alternative) or on Limpet's existing from-scratch posture
   (`limpet/critic.py`, `limpet/model_router.py`). The vocabulary worth lifting regardless of
   which runtime wins:
   - **Role/goal/backstory/tools as the agent-definition shape** (§1) — Limpet's stance agent
     prompt files already are this; naming it explicitly (as CrewAI's `Agent` constructor does)
     costs nothing and aids future plugin-authoring documentation.
   - **DelegateWork vs. AskQuestion as two distinct primitives** (§3), not one generic "message
     agent" tool — the split between "do this bounded task" and "answer this without doing
     work" maps cleanly onto recon→stance dispatch (DelegateWork: "read this evidence set, apply
     your stance") vs. reconcile→stance clarification (AskQuestion: "why did you flag this
     sink"), with the hard caveat that Limpet must invert CrewAI's default and keep
     `allow_delegation=False` between stances always (§3), never relying on the framework's
     own permission model to prevent cross-stance leakage.
   - **Scoped, read-many/write-narrow memory** (§7's `MemorySlice` vs. `MemoryScope` split) as
     the pattern to copy *if and when* Limpet builds persistent cross-session memory — reconcile
     gets a read slice over every stance's scope, each stance gets write access to only its own.
3. **Reject regardless of which path wins:** the 75-tool catalog (§6, no RE relevance), CrewAI's
   `Process.hierarchical` as a literal stand-in for recon (§4, wrong re-planning granularity),
   and any reliance on CrewAI's (or anyone's) delegation-tool permission model as the enforcement
   mechanism for stance isolation — that enforcement has to be Limpet's own, same conclusion the
   ClawSwarm dissection reached independently (`research/WQ4347_CLAWSWARM_DISSECTION.md` §6,
   "Stance isolation... must be Limpet's, not [the framework's]").

**Net:** CrewAI does not change what Limpet must build (the finding schema, the adjudication
gate, RE-specific tools) — it only changes how much orchestration plumbing Limpet would have to
write versus import. That is a real build-time tradeoff, not a capability gap, and the honest
answer is that it is a viable fast-path if schedule pressure wins, and a vocabulary donor (not a
runtime donor) if control and dependency-surface minimization wins — consistent with the
ticket's own framing of the decision.
