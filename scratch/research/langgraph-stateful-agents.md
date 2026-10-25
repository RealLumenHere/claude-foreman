# LangGraph stateful agents — design for the Limpet Agent Processor pilot

Design doc for wq#4636. The broad framework evaluation (verdict, SDK choice,
checkpointer wiring, framework comparison) lives in
`research/wq4630_langgraph_stateful_agent_graphs.md`; this doc is the concrete
graph spec that `projects/limpet/src/limpet/agent/graph.py` implements. Section 3
is the pilot graph.

## 1. Scope

One Limpet episode, recast from today's implicit planner/executor call-stack
(`limpet/planner.py:47`) into a resumable `StateGraph`. Not a fleet-wide rewrite
(that is the multi-ticket migration wq#4630 scoped) — one real graph that proves
the two primitives LangGraph adds: a human-approval pause and a long-running-tool
pause, both checkpointed so a turn survives a process restart.

## 2. State

A single `AgentState` TypedDict carries everything a mid-run checkpoint must
restore (nothing may hide in the Python call stack, or resume cannot rebuild it):
the goal, an append-reduced `history` warm band, the current planner decision and
its `action_class`, the `pending_tool`, the hot-plugged `device` + `device_handled`
flag, the human `approved` verdict, the tool `result`, and `done`/`answer`.

## 3. Graph (the pilot)

```
START -> planner
planner --{device_detected}--> target_wizard (subgraph) --> planner
planner --{action_class == read}------------------------> tool_call
planner --{action_class in (caution, destructive)}-------> sentinel
planner --{done}----------------------------------------> END
sentinel --{approved}--> tool_call
sentinel --{denied}----> result
tool_call -> executor
executor --{async tool}--> watchdog_wait --> result
executor --{sync tool}---------------------> result
result -> planner
```

Nodes:

- **planner** — wraps `limpet.planner.Planner.build_system_prompt()` + one model
  call (injected `plan_fn`); emits a decision dict: a tool call with an
  `action_class`, or `done`/`answer`. A missing `action_class` defaults to
  `destructive` (hal-spec.md §9.4(2), fail closed).
- **target_wizard** — a compiled subgraph entered whenever an unhandled `device`
  is present. Builds the numbered device menu (mirrors `limpet/hal_wizard.py`),
  `interrupt()`s for the operator's choice, records it, marks the device handled,
  returns to planner. Device detection pre-empts planning.
- **sentinel** — the human-in-the-loop gate (hal-spec.md §9.3). On a
  `caution`/`destructive` action it `interrupt()`s; the TUI resumes with CONFIRM
  (→ tool_call) or CANCEL (→ result, denial recorded, no dispatch). Per-invocation
  (§9.4(4)).
- **tool_call** — materializes the dispatch request the executor runs.
- **executor** — dispatches a synchronous tool inline (injected `tool_dispatch_fn`,
  default `langchain-mcp-adapters` → MCP Meta-Server); an `async_wait` tool is
  kicked off and routed to watchdog_wait.
- **watchdog_wait** — `interrupt()`s after an async dispatch; resumed out-of-band
  by `watchdog --callback` (wq#4629) via `resume_from_watchdog`, which feeds the
  finished result back as the interrupt's return value.
- **result** — folds the outcome (or a denial) into the warm band, clears the
  pending tool/approval, and re-plans.

## 4. Checkpointing

`PostgresSaver` against a dedicated database `limpet_graph` on lumen-pgvector —
**not** `fleet_memory`: the Python `PostgresSaver` takes no schema argument, so
isolation is per-database, and keeping the opaque checkpoint blobs out of
fleet_memory keeps them clear of the `mcp_ro` grant. `setup()` creates
`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`
in that DB (verified). Unit tests use `InMemorySaver`; the integration test uses
`PostgresSaver` and skips cleanly without the DB.

## 5. Model/tool agnosticism

`plan_fn` and `tool_dispatch_fn` are injected. Defaults wire `langchain-anthropic`
(system prompt from the existing `Planner`, so the graph composes with rather than
replaces today's context builder) and `langchain-mcp-adapters`, both imported
lazily so the in-memory tests need neither a key nor a network.
