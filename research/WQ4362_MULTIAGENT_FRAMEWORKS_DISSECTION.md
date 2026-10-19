# wq#4362 — Dissection: multi-agent frameworks for the Limpet RE hunter topology
### AutoGen actor model · Google ADK `RemoteA2aAgent` · Semantic Kernel plugins · CrewAI deep-dive

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Sourcing note (instinct 8 —
anti-fabrication):** every claim below came from `fleet-search web` on 2026-10-19 (SearXNG's
own engines were suspended/CAPTCHA'd this run — brave/duckduckgo/google-cse/startpage all
reported `Suspended`/`CAPTCHA`; the keyed DeepSeek Search fallback served results, per the
tool's own `note` field), not from memory. Primary sources pulled: `microsoft/autogen`
source (`conversable_agent.py`, `autogen_core` message/runtime docs and API reference),
`microsoft/autogen` dotnet `HumanInputMiddleware.cs`; the AG2AI team's own Google Developer
Forums post on AG2 0.10.0's native A2A support; `google/adk-python` source
(`invocation_context.py`), GitHub issue #5494, `adk.dev` live-configuration docs, and
third-party ADK ports (`adk_dart`, `zeroasterisk/adk-elixir`) as cross-language evidence;
`microsoft/semantic-kernel` source (`openapi_manager.py`), GitHub issue #12373, discussion
#10352, and the DeepWiki `SemanticKernelCookBook` language-comparison page; `crewai.com`
official docs (`concepts/collaboration`, `concepts/memory`, EN/KO/PT-BR locales — the KO
locale's own text supplied the composite-score phrasing most explicitly), GitHub PR #4420,
and a third-party production write-up (`callsphere.ai`) on Flow-hosts-Crew patterns; and two
independent secondary sources (`visualstudiomagazine.com`, `atlan.com`) on Microsoft Agent
Framework (MAF) 1.0 superseding both AutoGen and Semantic Kernel. Doc/URL citations inline
per section; `fleet-search --project wq4362` caches each query/result pair at
`/home/lumen/projects/wq4362/research/search_cache.jsonl` for replay.

Companion to the sibling LIMPET multi-agent dissections this ticket extends rather than
duplicates: `research/WQ4356_A2A_PROTOCOL_DISSECTION.md` (the wire/discovery contract —
Agent Card, Task, Artifact — already recommended as the only adoptable cross-boundary
protocol for the wq3999 topology), `research/WQ4361_AGENT_RUNTIME_PATTERNS_DISSECTION.md`
(OpenAI Agents SDK guardrails/sandboxes, Agno offloading, Pydantic AI validation-retry —
already mapped onto `limpet/critic.py`/`limpet/obligations.py`), and
`research/WQ4367_ORCHESTRATION_DISSECTION.md` (Symphony/Captain Claw/sheath — already
concluded "ticket-driven outer loop, DAG-based inner loop, two-stage verification at every
step" for Limpet's `design/wq3806_limpet_architecture.md` §6 investigation loop). This
ticket's job is narrower: is there a **named, general-purpose multi-agent framework**
(AutoGen, ADK, Semantic Kernel, or CrewAI) Limpet should take a runtime dependency on for the
`design/wq3999_rex_skill_dissection.md` §4 topology — **recon (alone) → five stance agents
(parallel, non-communicating) → reconcile (join)** — versus continuing to hand-build the
fan-out/join primitive the way `limpet/critic.py` and `limpet/obligations.py` are already
hand-built. Limpet's agent core is still design-only
(`design/wq3806_limpet_architecture.md` §6), so this is runtime research to shape that build,
not a retrofit onto running code.

---

## 1. AutoGen — event-driven actor model, and a fork the ticket's framing has to account for

Source: `microsoft/autogen` `autogen_core` docs
(`microsoft.github.io/autogen/stable/user-guide/core-user-guide/framework/message-and-communication.html`),
`microsoft.github.io/autogen/0.4.8/reference/python/autogen_core.html`, legacy
`autogen/agentchat/conversable_agent.py` (v0.2 API), dotnet `HumanInputMiddleware.cs`, AG2AI
Google Developer Forums post (Nov 12, 2025).

- **v0.2 → v0.4 was a full rewrite, sync → async.** The legacy (`pyautogen` v0.2)
  `ConversableAgent.human_input_mode` the ticket names (`ALWAYS`/`NEVER`/`TERMINATE`, confirmed
  verbatim against `conversable_agent.py`'s own `get_human_input()`/`_is_termination_msg()`
  logic — `NEVER` always auto-replies `"exit"` past the consecutive-reply cap, `TERMINATE`
  only prompts a human when a termination message is detected, `ALWAYS` prompts every turn)
  was a **synchronous, blocking** conversation loop. v0.4 (`autogen-core`) replaced that
  entirely with an async, event-driven actor runtime: agents subclass a routed-agent base,
  register `@message_handler` coroutines per message type, and a runtime
  (`SingleThreadedAgentRuntime` for a single process, or a distributed runtime) dispatches
  messages via `AgentRuntime.send_message()` (point-to-point, request/response) or
  `publish_message()` (topic-based broadcast, no reply expected) — the "actor model" framing
  in the ticket is literally this: agents are actors with mailboxes, not functions a
  controller calls in turn. An `InterventionHandler` registered on the runtime can inspect,
  rewrite, or `DropMessage` any in-flight message at `on_send`/`on_publish` time — a
  framework-level hook for exactly the kind of structural gate `limpet/critic.py`'s
  `deterministic_check()` already is, but applied to message traffic instead of a finished
  finding.
- **The human-input-mode enum is not carried into the v0.4 actor core.** It was a
  `ConversableAgent`-level behavior in the old synchronous loop; the new actor runtime has no
  equivalent built-in three-state knob — human-in-the-loop in v0.4 is built from the same
  primitives as everything else (a message handler that calls out and awaits a response, or
  the `InterventionHandler` hook pausing a message). This is a direct, confirmed break from
  the ticket's own framing of "per-agent human-input modes" as a first-class AutoGen feature:
  it is a first-class feature of the **legacy, now-superseded** API, not of the actor core the
  ticket also asks about.
- **AutoGen forked.** The original AutoGen team split from Microsoft and continues the
  project as **AG2** under open governance (AG2AI org), still shipping the `ag2`/`autogen`/
  `pyautogen` PyPI packages. AG2 0.10.0 (Nov 2025) added **native A2A protocol support** —
  AG2's own announcement: *"developers can now create multi-agent systems that work
  seamlessly with agents from LangChain, CrewAI, and any other A2A-compatible framework…
  [AG2 can] expose existing AG2 agents as A2A-compatible services… [and] connect to remote
  A2A agents built with any framework and use them as if they were native AG2 agents."*
  Meanwhile Microsoft's own line continued separately (see §5) — "AutoGen" is now two
  diverging things with the same name, and a build-vs-buy decision has to say explicitly
  which one it means.

## 2. Google ADK — `RemoteA2aAgent` as reference implementation, with a documented circuit-breaker gap

Source: `zeroasterisk/adk-elixir` `RemoteA2aAgent` docs (confirming the shape against a
third-party port, not just Google's own docs), `google/adk-python`
`invocation_context.py` (`increment_llm_call_count`/`LlmCallsLimitExceededError`), GitHub
issue #5494, `adk.dev/live/configuration`, `adk-labs.github.io/adk-docs/live/configuration`,
a third-party cross-framework benchmark (`code-with-rashid.github.io/agentic-arena`).

- **`RemoteA2aAgent` is a thin proxy, not a reimplementation.** Per the Elixir port's own
  type/doc comment (confirming the contract is stable enough to port, independent of
  Google's Python/Java originals): *"Agent that communicates with a remote A2A agent via A2A
  client. This agent wraps a remote A2A endpoint and forwards local events to it. It
  maintains a session with the remote agent using the `contextId` field."* This is exactly
  wq#4356's own framing of A2A's `Task`/`contextId` as the durable, resumable unit
  (`research/WQ4356_A2A_PROTOCOL_DISSECTION.md` §3) — ADK's contribution isn't a new protocol
  idea, it's a concrete, now cross-ported (`adk_dart`, the Elixir port, the Java SDK's
  `RemoteA2AAgent`) reference adapter proving the Agent-Card/Task/`contextId` contract is
  implementable uniformly across languages that aren't Google's own Python/Java pair.
- **`ADK_MAX_LLM_CALLS`/`max_llm_calls` is a real, measured circuit breaker — on one code
  path only.** A third-party cross-framework benchmark (`code-with-rashid.github.io/agentic-arena`)
  measured loop-cap enforcement against a mock that never stops requesting tools: *"a budget
  of N produces exactly N requests on the wire — no off-by-one, no uncapped default. Compare
  Pydantic AI (retries is not a loop cap: 50 calls on a budget of 6), Agent Framework
  (uncapped by default: 41), and smolagents (one call beyond max_steps). ADK needed no
  correction at all."* `invocation_context.py`'s `increment_llm_call_count` backs this with a
  real exception (`LlmCallsLimitExceededError`) rather than a silently truncated run.
- **The gap: `max_llm_calls` does not cover `run_live()`.** `adk.dev/live/configuration`'s own
  text: *"It does not apply to `run_live()`. The parameter only guards the `run_async()` path,
  so a live session gets no automatic cost ceiling from it."* GitHub issue #5494 is the lived
  consequence: a live/streaming session that exhausts the internal call counter **completes
  silently** — no exception, no terminal event, the async generator just stops yielding, and
  the only documented workaround is the caller detecting the dead generator and starting a
  whole new session. This is a concrete counter-example to copying ADK's circuit breaker
  uncritically: the budget mechanism is real and well-built on the path it covers, and
  silently absent — not degraded, **absent** — on the other.

## 3. Semantic Kernel — OpenAPI-schema plugins, multi-language parity that is approximate, not exact

Source: `microsoft/semantic-kernel` `python/semantic_kernel/connectors/openapi_plugin/openapi_manager.py`,
GitHub issue #12373, discussion #10352, DeepWiki `SemanticKernelCookBook`
language-specific-implementations page.

- **A plugin from an OpenAPI document is a first-class import path, not a convention.**
  `openapi_manager.py`'s `create_functions_from_openapi(plugin_name, openapi_document_path,
  execution_settings, ...)` parses an OpenAPI spec (YAML or JSON) and emits one kernel
  function per operation — `kernel.add_plugin_from_openapi()` is the public entry point
  (confirmed against both the source and issue #12373's own reproduction call). Per
  discussion #10352 (an SK maintainer's reply), **the Python SDK supports OpenAPI v3+,
  the C# SDK supports both 2.0+ and 3.0+** — version support is not identical across
  language SDKs even for this one feature.
- **A real, acknowledged rough edge:** `add_plugin_from_openapi()` hard-codes a 5-second HTTP
  timeout with no configuration surface in the reported version (issue #12373); the
  documented workaround is constructing a custom `httpx.AsyncClient(timeout=...)` and passing
  it via `OpenAPIFunctionExecutionParameters(http_client=custom_client)` rather than a
  first-class timeout parameter — a plugin pointed at a slow backend fails by default, not by
  configuration choice.
- **"Multi-language parity" means consistent concepts at different version numbers, not one
  shared release train.** DeepWiki's own comparison table (.NET/Python/Java) shows **current
  versions 1.16.2 / 1.3.0 / 1.2.0 respectively** at time of capture — the three SDKs share a
  `Kernel`/plugin/function vocabulary and each adapts it to the host language's idiom
  (attributes vs. decorators vs. annotations; `Task`-based vs. `async`/`await` vs.
  `CompletableFuture`), but they are not the same artifact shipped three times — a plugin
  contract validated against the Python SDK's current OpenAPI-import behavior is not
  automatically validated against the .NET or Java SDK's behavior at their own current
  versions.

## 4. CrewAI deep-dive — Crews vs. Flows, the two delegation primitives, and the memory system's composite score

Source: `docs.crewai.com` `concepts/collaboration` (EN/PT-BR), `concepts/memory` (KO — the
locale whose text named the scoring formula most explicitly), GitHub PR #4420 ("New Unified
Memory System"), `callsphere.ai` production write-up.

- **`allow_delegation=True` grants exactly two named tools, confirmed against the official
  docs' own code comments:** `Delegate work to coworker(task: str, context: str, coworker:
  str)` and `Ask question to coworker(question: str, context: str, coworker: str)` — the
  ticket's "DelegateWork/AskQuestion primitives" named precisely. These are LLM-invoked tool
  calls inside a `Crew`'s own agent-to-agent turn, not a separate orchestration layer; a
  delegating agent decides whether and whom to delegate to by choosing to call one of these
  two tools mid-reasoning.
- **Crews and Flows are a deliberate, documented split, not two names for one thing.** A
  `Crew` is role-based agent collaboration — high autonomy, output emerges from delegation and
  discussion, low external control. A `Flow` is a plain Python class using `@start`/`@listen`
  decorators to chain methods with explicit state and branching — deterministic control, each
  node optionally invoking an LLM or kicking off a `Crew`. The documented 2026 production
  pattern (`callsphere.ai`, independently consistent with CrewAI's own docs): *"Flows host
  Crews — the Flow is the deterministic spine, the Crews are the LLM-heavy nodes."* A bare
  `Crew` is recommended only for short, non-branching, role-based work; anything needing
  conditional routing, retries, or persisted state should be a `Flow` with `Crew`s as nodes.
  This is the same split wq#4367 already drew for Limpet's own loop
  (`research/WQ4367_ORCHESTRATION_DISSECTION.md`'s "ticket/obligation-driven outer loop,
  DAG-based inner loop") under different names — CrewAI's `Flow` is wq4367's DAG-with-explicit-dependencies
  idea, and CrewAI's `Crew` is the bounded-autonomy-collaboration unit a DAG node can call.
- **The unified memory system's retrieval is a documented composite score, not bespoke
  recall-by-vector-similarity-alone.** PR #4420 replaced separate short-term/long-term/entity/
  external memory types with one `Memory` class; the official docs (KO locale, most explicit
  phrasing found): storage-time, an LLM infers scope/category/importance for each memory
  record, and retrieval (`memory.recall(...)`) ranks candidates by **"a composite score
  blending semantic similarity, recency, and importance"** (의미 유사도, 최신성, 중요도를
  혼합한 복합 점수) for "adaptive-depth recall." Memories can be scoped privately per agent
  or shared across the crew (PT-BR docs: a researcher can hold a private `/agent/researcher`
  scope while a writer reads the crew's shared memory with none of its own) — scope is a
  per-agent configuration choice, not an architectural constant.
- **Sequential-Crew is the wrong shape for the wq3999 hunter topology, confirmed rather than
  assumed.** A `Crew`'s delegation tools are peer-initiated and conversational — an agent
  decides mid-turn whether to delegate, and a delegate's result folds back into the same
  conversation thread. The wq3999 topology needs the opposite: `re-recon` must finish **alone**
  before anything else starts, the five stance agents must run **in parallel** and **must not
  see each other's output** (`design/wq3999_rex_skill_dissection.md` §4: "may not see another's
  findings"), and `re-reconcile` joins only after all five report. That's a fixed fan-out/join
  shape with an enforced non-communication constraint between siblings — the ticket's own
  framing ("better fit for async hunter topology than CrewAI sequential") is correct and this
  research confirms it from CrewAI's own documented collaboration model, not just from the
  topology's description.

## 5. The fact that reframes all four: Microsoft has already merged two of them

Source: `visualstudiomagazine.com` (Apr 6 2026 piece on Microsoft Agent Framework 1.0),
`atlan.com` Semantic Kernel status page.

- **Microsoft Agent Framework (MAF) 1.0 shipped April 3, 2026, explicitly combining Semantic
  Kernel and AutoGen into one SDK.** Visual Studio Magazine's own framing: *"Microsoft Agent
  Framework is the company's attempt to provide one developer stack... It combines work that
  previously lived in separate Microsoft efforts, especially Semantic Kernel and AutoGen,
  into a single open-source framework."*
- **Semantic Kernel is now maintenance-mode by Microsoft's own admission, reported
  independently.** Atlan's framework-status page: *"Microsoft continues to address critical
  bugs and security issues in Semantic Kernel, but the majority of new features are built for
  Agent Framework... Fragmentation with AutoGen: Developers were confused about which
  framework to use, features overlapped, and the community was split. The launch of Agent
  Framework resolved this directly."* MAF also ships ready-made multi-agent patterns
  (sequential, concurrent, group chat, handoff, magentic) that Semantic Kernel left to the
  caller to hand-write.
- **Consequence for this ticket's framing, stated plainly:** "AutoGen" (Microsoft's line, not
  the AG2 fork) and "Semantic Kernel" are not two independent long-term options any more —
  they are two predecessor projects to the same successor SDK, and Microsoft's own
  stated roadmap directs new feature investment there, not into either legacy name. A
  build-vs-buy decision made against either name in isolation is implicitly a decision against
  a framework whose own vendor has told the market to move off it.

## 6. LIMPET VALUE — framework recommendation for the wq3999 hunter topology

**Recommendation: build the fan-out/join primitive in-house; adopt no framework dependency
for the runtime; reserve A2A (already decided in wq#4356) as the only adoptable wire-level
contract, for if/when the topology ever needs to cross a process or machine boundary.** This
is a single choice, not a hybrid of frameworks — but it borrows two documented *patterns*
(not dependencies) from this research, named explicitly so a future build doesn't have to
rediscover them:

1. **Borrow AutoGen's actor-model shape, not the `autogen-core` package.** The
   `publish_message`/topic-subscription pattern (§1) is the right *shape* for "recon finishes,
   then broadcast its evidence set to five subscribed, non-communicating hunter agents, then
   reconcile joins on all five replies" — it is a closer structural match than CrewAI's
   peer-delegation model (§4) because nothing in it lets a hunter call back to another hunter.
   But Limpet's actual concurrency need is five `asyncio` tasks inside one process on one
   16GB appliance (`design/wq3806_limpet_architecture.md` §6), not cross-process actor
   dispatch — the problem the AutoGen/MAF actor runtime is built to solve (distributed agents,
   pluggable transports, intervention handlers across a wire boundary) is bigger than the
   problem Limpet has today. Hand-building the five-task fan-out plus a join barrier is a few
   hundred lines on top of what `limpet/critic.py` and `limpet/obligations.py` already
   establish as the house pattern (hand-built, framework-free, cited by file:line), not a
   reason to add a fast-moving external dependency (§1's own fork history and §5's
   MAF-absorption are both evidence that whichever of these packages Limpet picked eighteen
   months ago would already need a migration today).
2. **Borrow ADK's loud-circuit-breaker discipline, not `RemoteA2aAgent` or `max_llm_calls`
   itself.** §2's lesson generalizes past ADK: a per-hunter call/step budget must fail with an
   explicit, caller-visible error on every code path the hunter can take, including whatever
   Limpet's equivalent of ADK's uncovered `run_live()` path turns out to be (a long-running
   emulation/fuzzing hunter, per `research/WQ4356_A2A_PROTOCOL_DISSECTION.md` §7 item 4's
   `CancelTask`/budget-abort case). `limpet/model_router.py`'s `resolve_model` already routes a
   step to a model by capability (`limpet/model_router.py:47`); the missing piece this ticket
   surfaces is a call-count ceiling per hunter dispatch that raises instead of letting a
   runaway hunter silently exhaust its budget the way ADK's `run_live()` path does today.
3. **Do not adopt Semantic Kernel.** Its multi-language plugin parity (§3) solves "my agent
   core and my tools are written in different languages" — Limpet's agent core, `critic.py`,
   `obligations.py`, and `model_router.py` are all Python today, and §3's own finding (parity
   is approximate, pinned to different version numbers per language) means adopting it buys
   a cross-language guarantee Limpet doesn't need at a cost (an OpenAPI-schema layer between
   every hunter and its tools) Limpet doesn't need to pay. Combined with §5's maintenance-mode
   finding, there is no case for taking this dependency now.
4. **Do not adopt CrewAI as a dependency, but keep its composite-memory-score formula as a
   named pattern for later.** §4 confirms the sequential/peer-delegation `Crew` model is the
   wrong shape for the hunter topology, exactly as the ticket's own framing predicted. If a
   future ticket gives Limpet's agent core a long-session memory beyond the per-workspace
   `evidence/`/`obligations.json` store it already has, CrewAI's documented formula — rank by
   a blend of semantic similarity, recency, and importance, with importance itself inferred by
   an LLM call at storage time (§4) — is a concrete, already-proven shape to copy the *idea*
   of, not the package.
5. **RemoteA2aAgent confirms, it does not add to, wq#4356's existing call.** §2's cross-language
   ports (Dart, Elixir, Java) are independent evidence that the Agent-Card/Task/`contextId`
   contract wq#4356 already recommended adopting is implementable uniformly — useful
   confirmation, not a new recommendation. Nothing in this research changes wq#4356 §7's
   adopt/build-separately split.

**The one-line answer the ticket asks for:** no single named framework, and no hybrid of
named frameworks either — build the hunter fan-out/join primitive as a small, in-house
extension of the pattern `limpet/critic.py`/`limpet/obligations.py` already establish,
borrowing AutoGen's actor-model *shape* and ADK's loud-circuit-breaker *discipline* as
design patterns, keeping A2A (wq#4356) as the one protocol-level dependency reserved for a
future cross-process need, and treating CrewAI's memory-scoring formula as a pattern worth
revisiting only if a later ticket adds long-session memory to the agent core.
