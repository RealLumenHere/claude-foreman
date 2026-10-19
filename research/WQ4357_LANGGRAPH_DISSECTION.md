# wq#4357 — Dissection: LangGraph (StateGraph) for the Limpet Target Device Wizard

**Date:** 2026-10-19 · **Lane:** LIMPET research · **License:** MIT (`langchain-ai/langgraph`,
Copyright (c) 2024 LangChain, Inc. — confirmed via `unpkg.com/@langchain/langgraph` LICENSE
file and the Oracle third-party-notices mirror; adoptable, wrap/vendor/learn per the wq#3959
license audit). **Sources:** docs.langchain.com official docs (`/oss/python/langgraph/`
concepts, how-tos, and `/errors/MULTIPLE_SUBGRAPHS`), `langchain-ai/langchain-skills`
`langgraph-persistence` and `langgraph-human-in-the-loop` SKILL.md files, `langchain-ai/langgraph`
GitHub issue #6735 and PR #6751 (compile-time detached-node detection), `docs.langchain.com/oss/python/langgraph/case-studies`
(official customer list), agentmarketcap.ai Fortune-500 production writeup — fetched via
`fleet-search web` 2026-10-19 (SearXNG general engines suspended/CAPTCHA'd; DeepSeek Search
fallback used, per the tool's own `note` field). Doc URLs inline per section.

Companion to `docs/target-wizard-spec.md` (the wq#3901 Target Device Wizard this ticket asks
LangGraph to be modeled against) and the sibling LIMPET protocol dissections
(`research/WQ4356_A2A_PROTOCOL_DISSECTION.md`, `design/wq3999_rex_skill_dissection.md`). Where
wq#4356 dissected a wire protocol for cross-agent delegation, this one dissects a **runtime** —
LangGraph doesn't replace A2A's Task/Artifact contract or REx's recon/hunters/reconcile
methodology, it's a candidate execution substrate underneath both: the Target Device Wizard's
`WizardSession` (`docs/target-wizard-spec.md:102-107`) is already hand-specified as a state
machine with a stage, a log of transitions, and on-disk persistence for resumability — exactly
the three things LangGraph's `StateGraph` + checkpointer give for free. This is runtime research
to shape a build, not a retrofit onto running code — the wizard itself is still design-only.

---

## 1. State / Nodes / Edges as first-class primitives

Source: `docs.langchain.com/oss/python/langgraph/graph-api` (concepts).

- **State** is a typed schema (a `TypedDict`/Pydantic model or plain dict with reducers) that
  every node reads and partially updates. A node is a plain Python (or JS) callable:
  `(state) -> partial_state_update`. Nothing distinguishes a "framework" node from a bare
  function — the callable can run an SQL query, call a vision OCR pipeline, or call an LLM.
  There is no special node type for "agent step" vs "deterministic step"; the graph doesn't
  care what's inside the function, only its state-in/state-out contract.
- **Edges** are either static (`add_edge(a, b)` — always run b after a) or conditional
  (`add_conditional_edges(a, routing_fn, {value: node, ...})` — `routing_fn(state)` returns a
  key that selects the next node). Routing is a function of **state**, not of which node ran —
  the same mechanism handles "branch on device class" and "branch on confidence score" because
  both are just fields in the state object the routing function reads.
- This maps directly onto `docs/target-wizard-spec.md:54-58`'s own one-sentence flow
  (`detect → identify → suggest workflow → create workspace → begin analysis`) and the §4
  state diagram: each stage already named there (DETECT, IDENTIFY, ASSIST, SUGGEST, WORKSPACE
  CREATE, ANALYZE) is a node; the `known` vs `unknown/probable` fork out of IDENTIFY
  (`target-wizard-spec.md:112-124`) is a conditional edge keyed on
  `target_profile.identification.status`.

## 2. StateGraph compilation — real but partial validation

Source: `docs.langchain.com` graph-api docs; `langchain-ai/langgraph` GitHub issue #6735
("request: Compile-time validation of nodes that aren't statically linked") and PR #6751
("fix(langgraph): add compile-time detection of detached nodes").

- `.compile()` does catch some structural errors today: a conditional edge whose routing
  function can return a value with no matching node, or a path that can never reach `END`,
  raises at compile time (the "END node unreachable" class of error, independently confirmed
  by a third-party troubleshooting page as a real, user-hit error).
- **It does not catch every orphan.** Issue #6735 demonstrates a node added via `add_node()`
  but never wired to any edge compiles cleanly with no warning — "Graph compiles successfully
  (no warning about orphan node)" is the issue's own reproduction output. PR #6751 proposes
  (as of this research) *optional* compile-time detection of exactly this — nodes unreachable
  from `START` or with no path to `END` — meaning the gap was still open at time of writing,
  not yet a default guarantee.
- **Consequence for Limpet:** "every stage reachable, no orphans" is a property compile() helps
  with but does not fully guarantee out of the box. Keep `docs/target-wizard-spec.md:258-277`'s
  own acceptance-probe discipline (fixture-driven known/unknown/parity/hygiene tests) as the
  real correctness gate; treat `.compile()` as a cheap first-pass lint, not a substitute for
  the spec's own test plan. This is the same posture the fleet already takes toward any
  framework validation step — verify, don't assume the tool's internal check is complete.

## 3. Durable execution + checkpointing — replayable by construction

Source: `langchain-ai/langchain-skills` `langgraph-persistence` SKILL.md;
`docs.langchain.com/oss/python/langgraph/persistence`; `docs.langchain.com/oss/python/langgraph/interrupts.md`.

- A compiled graph takes a **checkpointer** (`InMemorySaver` for dev, `SqliteSaver`/
  `PostgresSaver` for production — swappable backend, same `BaseCheckpointSaver` interface).
  Every superstep (one round of node execution) commits a checkpoint keyed by
  `{"configurable": {"thread_id": "..."}}` — the thread is the resumable unit, directly
  analogous to the wizard's `session_id` (`docs/target-wizard-spec.md:104-107`).
- **Resume is `invoke(None, config)`** — passing no new input against an existing
  `thread_id` continues from the last checkpoint rather than restarting. This is the literal
  mechanism the ticket means by "wizard doesn't lose intelligence when the process restarts
  after an approval gate": the gate doesn't need its own bespoke resume logic, it's the
  checkpointer's default behavior.
- **`getStateHistory()` / replay / fork.** The checkpointer retains the full history of
  checkpoints for a thread, not just the latest. A caller can iterate past states, re-invoke
  from any one of them (`invoke(None, past.config)`), or **fork**: call `updateState(past.config,
  {...})` to edit state at a past checkpoint and continue from the edited version, producing a
  new branch without mutating the original history. `docs/target-wizard-spec.md:70-71` already
  states the design requirement this satisfies verbatim: "Each stage is a committed transition
  in the session record, so an onboarding is itself replayable and auditable" — that's exactly
  `getStateHistory()`, not a feature to hand-build on top of a flat `log[]` array
  (`target-wizard-spec.md:104-107`).
- **Three checkpointer modes for a subgraph**, per the `langgraph-persistence` skill's own
  comparison table — relevant to §7 below, not just the top-level wizard graph:
  - `checkpointer=False` — no persistence, no interrupts, cheapest; fine for a subgraph that
    never needs to pause and never needs multi-turn memory.
  - `checkpointer=None` (default / inherited) — pauses/resumes (`interrupt()` works) within one
    invocation, but no cross-invocation memory; **multiple parallel calls to the same compiled
    subgraph are fine** in this mode.
  - `checkpointer=True` ("continuations mode") — full multi-turn memory, but calling the *same*
    compiled subgraph more than once inside one node/step raises `MULTIPLE_SUBGRAPHS`
    (`docs.langchain.com/oss/python/langgraph/errors/MULTIPLE_SUBGRAPHS.md`: "This error occurs
    when you call a subgraph inside a node multiple times, and the subgraph is compiled with
    `checkpointer=True`"). The fix the docs give is exactly the mode selection above — drop to
    `False` or the default `None`, or give each call its own distinct subgraph instance.

## 4. Mixing deterministic and LLM-driven steps in one graph

No special casing exists in the API for this — it falls directly out of §1. A node is "do I
call an LLM" entirely by what the function body does; the graph's job (state schema, edges,
checkpointing) is identical either way. `docs/target-wizard-spec.md:138-169`'s own IDENTIFY
stage is already this exact mix in miniature: the VID:PID resolution path (§4.2 steps 1-3) is
pure deterministic lookup against `usb.ids` and the plugin manifest; the vision-assisted path
(same section) calls out to the wq#3900 OCR pipeline, an ML component. Under LangGraph both are
just two branches (or two sequential node calls) writing into the same `TargetProfile.identification`
fields — no architectural seam between "the deterministic part of IDENTIFY" and "the LLM/vision
part of IDENTIFY" the way there would be if they were separate orchestration layers.

## 5. Human-in-the-loop as a runtime primitive, not a bolt-on

Source: `docs.langchain.com/oss/python/langgraph/interrupts.md`;
`langchain-ai/langchain-skills` `langgraph-human-in-the-loop` SKILL.md.

- **`interrupt(payload)`** called from inside any node pauses the entire graph at that point,
  persists the pause in the checkpoint, and surfaces `payload` to whatever is driving the graph
  (CLI, GUI, MCP caller). Resuming passes a value back in — `Command(resume=value)`, or in the
  docs' own phrasing, "pause execution and wait for external input" — and the node's call to
  `interrupt()` returns that value and the function continues from that exact line, not from
  the top of the node. This is a genuine runtime primitive: the pause point is wherever the
  node author put the call, not a separate pre/post-node hook layered over the graph.
- **Static alternative:** `interrupt_before=[...]`/`interrupt_after=[...]` at compile time pause
  unconditionally before/after named nodes — useful for "always stop before this node" gates;
  dynamic `interrupt()` is for "stop only if this specific condition inside the node is true,"
  which is the shape the wizard needs (§4.3 gate fires only on an energize-step, not on every
  node).
- **Validation loop pattern** (from the `langgraph-human-in-the-loop` skill): call `interrupt()`
  in a loop to re-prompt until the resumed value passes a check, then proceed — this is a
  direct, off-the-shelf implementation of `docs/target-wizard-spec.md:127-128`'s "ASSIST can
  loop (operator adds evidence, re-identifies) until confidence clears the §4.3 gate."
- **Direct mapping onto the confidence gate** (`target-wizard-spec.md:171-184`): the node that
  would drive an energize-step (apply probe power, drive JTAG/SWD, write flash) calls
  `interrupt({"reason": "confidence 0.6 below 0.85, confirm to proceed", "profile": ...})`
  whenever `confidence < 0.85`; the operator's `--confirm` flag
  (`target-wizard-spec.md:245-247`) becomes the value passed to `Command(resume=True)`. The
  spec's hand-specified "prints the blocking reason and exits non-zero without `--confirm`"
  behavior is what the runtime already does by default — a resume value is required before
  execution proceeds past that point, headless or not.

## 6. Handoffs via state-driven routing

Source: `docs.langchain.com` graph-api docs; `nexus-substrate/nexus-agents` PR #6006 (third-party
confirmation of `Command.goto` semantics against upstream LangGraph).

- **`Command(goto=target, update={...})`** returned from a node both updates state and redirects
  execution to `target` — dynamic routing decided by the node's own logic at runtime, not fixed
  at graph-definition time the way a plain `add_edge` is. `add_conditional_edges` is the
  declared-at-compile-time version (route by a separate routing function reading state);
  `Command.goto` is the decided-at-run-time version (the node itself decides, inline). Both
  share the same underlying principle the ticket names: routing is a read of state, never a
  hardcoded call graph.
- **`Send`** fans a single node out to N parallel invocations of a target node, one per item in
  a list the dispatching node produces — the primitive for "dispatch five non-communicating
  stance agents against one recon evidence set in parallel" (§7 below), as opposed to five
  separately-wired static edges.

## 7. Production pedigree

Source: `docs.langchain.com/oss/python/langgraph/case-studies` (LangChain's own list, "compiled
from public sources"):

| Company | Industry | Use case | Reference |
|---|---|---|---|
| J.P. Morgan | Financial Services | Copilot for domain-specific task | Interrupt talk, 2025 |
| Klarna | Fintech | Copilot for domain-specific task | Case study, 2025 |

Uber is independently and repeatedly cited (agentmarketcap.ai Fortune-500 production piece,
GitNation conference talk abstracts) for using LangGraph's subgraph support to spawn
specialist sub-agents per language/repo context for large-scale code migration, coordinated
through a supervisor graph — the agentmarketcap piece frames this as "each sub-agent operates
statelessly from its own perspective but contributes to a shared parent graph state," which is
precisely the parent-graph/subgraph shape in §8 below. This is secondary-source framing
(a blog, not LangChain's own case-studies table), flagged as such rather than asserted with the
same confidence as the J.P. Morgan/Klarna table entries.

## 8. LIMPET VALUE — concrete mapping

**Target Device Wizard as a `StateGraph`.** Every node in
`docs/target-wizard-spec.md:109-125`'s state diagram becomes a graph node operating on a
`TargetProfile`/`WizardSession`-shaped state schema (already specified at
`target-wizard-spec.md:75-107` — no new data model needed, LangGraph consumes the one that
exists):
- `DETECT`, `IDENTIFY`, `SUGGEST`, `WORKSPACE_CREATE`, `ANALYZE` — static edges in sequence.
- `ASSIST` — reached by a conditional edge off `IDENTIFY` keyed on
  `identification.status in {probable, unknown}`; loops back to `IDENTIFY` via its own
  conditional edge (§5's validation-loop pattern) until an `interrupt()`-gated operator
  override or higher-confidence evidence clears it, matching "every state is re-enterable"
  (`target-wizard-spec.md:127`) verbatim.
- The §4.3 confidence gate becomes `interrupt()` calls inside whichever node is about to run
  an energize-step, not a separate flag check threaded through every CLI command
  (`target-wizard-spec.md:245-247`) — one runtime mechanism instead of a convention every new
  command has to remember to apply.

**Checkpointer replaces the hand-rolled session file.** Swap
`/var/limpet/sessions/<session_id>.json` (`target-wizard-spec.md:106-107`) for a
`SqliteSaver`/`PostgresSaver` keyed on `thread_id = session_id`. This is a direct, better-for-free
replacement: §3's `getStateHistory()`/fork gives the "onboarding is itself replayable and
auditable" requirement (`target-wizard-spec.md:70-71`) as a library call instead of a bespoke
`log[]` append-and-replay implementation, and "resume from checkpoint" is `invoke(None, config)`
rather than code that re-reads and re-interprets a JSON log on restart.

**Conditional edges for intelligence-confidence routing.** The §4.3 thresholds (`≥0.85` /
`0.4–0.85` / `<0.4`) are a direct `add_conditional_edges` routing function reading
`state.target_profile.identification.confidence` — the same mechanism independently covers
"branch on device class" (§4.4's profile→arc table, `target-wizard-spec.md:194-199`) and
"branch on confidence," because both read fields off one state object.

**recon → hunters → reconcile as parent graph + subgraphs**
(`design/wq3999_rex_skill_dissection.md` §4, the 7-agent topology): `re-recon` is a single
node/subgraph that must complete alone before anything else runs — a static edge, not a
conditional one, enforcing "everything downstream reads its output" (wq3999). The five stance
agents (`re-bughunt`, `re-safety`, `re-arithmetic`, `re-lifecycle`, `re-logic`) are **one
compiled "hunter" subgraph invoked five times in parallel** via `Send`, each call parameterized
by which stance prompt/tool subset to run — not five separately-wired subgraphs, since their
only difference is which stance's instructions they load against the same recon evidence.
**This is exactly the case §3's `MULTIPLE_SUBGRAPHS` finding warns about:** compiling the
hunter subgraph with `checkpointer=True` and invoking it five times inside one parent node/step
raises that error outright. The correct mode is the default (`None`/inherited) — each parallel
hunter invocation still gets `interrupt()`-capable pause/resume if a stance needs to pause (e.g.
a hunter blocked on a budget/time-box decision) without hitting the same-subgraph collision,
at the cost of "state inspection: current invocation only" per §3's mode table. If reconcile
later needs to audit one specific hunter's full checkpoint history after the fact (not just its
final Artifact-equivalent output), that hunter's dispatch needs its own distinct `thread_id`
per invocation — `checkpointer=True` with five separately-threaded invocations, not five calls
sharing one — a design choice to make explicitly, not discover via the error at build time.
`reconcile` is the join node after the `Send` fan-out completes, reading all five hunters'
outputs from parent state — mirroring A2A's `ListTasks`-before-adjudicate pattern already
specified in `research/WQ4356_A2A_PROTOCOL_DISSECTION.md` §7 item 4, giving the same ticket two
independently-converging designs for the same join point (protocol-level via A2A, runtime-level
via LangGraph) that should be implemented as one mechanism, not two.

**Build separately, outside LangGraph (same posture as wq#4356 §6):** authorization for who may
approve a reconcile verdict or launch an energize-step is the fleet's own broker/allowlist/gate
plane, not something `interrupt()`'s resume value encodes on its own — a resume *unblocks*
execution, it doesn't *authenticate* who supplied it. LangGraph gives the pause/resume
mechanism; the policy of who is allowed to resume stays Limpet's to build, same boundary §6 of
wq#4356 draws for A2A's `securitySchemes`.
