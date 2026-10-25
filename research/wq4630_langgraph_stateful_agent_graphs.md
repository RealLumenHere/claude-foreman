# wq#4630 — RESEARCH: LangGraph — stateful agent graphs with checkpoint-to-Postgres

## Verdict
**Worth a scoped pilot, not a wholesale rewrite.** LangGraph's graph model
(nodes + edges + a checkpointer) is a closer structural match to the Limpet
planner/executor split (`limpet/planner.py:47`'s `Planner` class, consumed by an
executor loop) than our current linear-chat agent loop is, and the Postgres
checkpointer (`langgraph-checkpoint-postgres`, PyPI) can sit next to our existing
pgvector store rather than replace it. But adopting it fleet-wide means porting
every node boundary in Limpet to LangGraph's `StateGraph` API and taking on a
new dependency with its own release cadence — that is a multi-ticket migration,
not this ticket's scope. Recommendation: build one real graph (planner → tool
call → wait-for-result → Sentinel approval → resume) as a pilot against a single
Limpet flow before deciding whether to migrate the rest.

## What LangGraph is
LangGraph (`langchain-ai/langgraph`, Python + TS) models an agent as a directed
graph of nodes (each a function/LLM call) and edges (including conditional
edges), executed by a runtime called Pregel. State flows through the graph as a
typed dict/object; each superstep snapshots state via a **checkpointer**.
Checkpointers ship for Postgres (`langgraph-checkpoint-postgres`), SQLite, and
Redis (community). Because every superstep is checkpointed, a run can be
interrupted at any node and resumed later from the last checkpoint — including
across process restarts — which is the "pause for a human" primitive
(`interrupt()` / human-in-the-loop) the ticket is after.

## Python SDK vs TypeScript SDK
Both are first-class and released together from the same monorepo
(`libs/checkpoint-postgres` lives under the same repo as the JS packages).
Python is the more mature surface for backend/agent-orchestration use (richer
ecosystem of checkpoint backends, most tutorials and the LangChain tool
ecosystem are Python-first). TypeScript trails slightly on checkpointer
coverage and community examples. Limpet's planner/executor and the MCP
Meta-Server are already Python, so Python SDK is the only sane choice here —
no cross-language FFI needed.

## Postgres checkpointer: wiring to our pgvector store
`PostgresSaver` (from `langgraph.checkpoint.postgres`) owns its own tables
(`checkpoints`, `checkpoint_writes`, `checkpoint_blobs` — created by
`PostgresSaver.setup()`) in whatever Postgres database/connection you hand it.
It does **not** use pgvector itself — checkpoints are opaque serialized
blobs/JSON, not embeddings. The practical integration is: point `PostgresSaver`
at the *same Postgres instance* that hosts our pgvector tables (same host, a
separate schema or database), not at pgvector as a vector store. Keep
embedding-based memory search (pgvector) and step-by-step execution state
(LangGraph checkpoints) as two separate concerns sharing one Postgres box —
conflating them would mean writing a custom `BaseCheckpointSaver` to shove
checkpoint blobs into a vector table, which buys nothing over the stock saver.

## Multi-agent coordination
LangGraph supports multi-agent patterns via subgraphs (a node that is itself a
compiled graph) and a "supervisor" pattern (one node routes to worker
subgraphs/nodes via conditional edges based on LLM output). This maps onto a
planner-dispatches-to-tool-executors shape directly: the planner node decides
which tool-node to invoke next, each tool-node can itself be a subgraph that
calls the MCP Meta-Server, and a conditional edge returns control to the
planner after each tool result — which is exactly the loop implied by
`limpet/planner.py`'s system-prompt build (it already assembles an "HAL
inventory" + "workspace manifest" + "warm band" context per turn, i.e. it is
already reconstructing state each call rather than resuming a checkpoint).

## Modeling the Limpet planner/executor split as a graph
Proposed graph for one Limpet episode, built on current code:
- `plan` node — wraps `Planner.build_system_prompt()` (`limpet/planner.py:54`)
  + one LLM call; outputs a tool call or a final answer.
- `tool_call` node — dispatches to the MCP Meta-Server; conditional edge back
  to `plan` on success.
- `wait_for_result` node — only needed for async/long-running tools; LangGraph
  supports this as a node that re-enters on the next invocation after an
  external event resolves (poll-and-checkpoint, not a blocking sleep).
- `sentinel_approval` node — calls `interrupt()`, which checkpoints state and
  returns control to the caller; Sentinel's human-in-the-loop gate resumes the
  run later by calling the graph again with the same `thread_id`, continuing
  from that checkpoint. This is the one new primitive LangGraph adds over what
  Limpet does today — Sentinel currently has no way to pause and resume a
  half-finished agent turn; `interrupt()` + `PostgresSaver` gives it exactly
  that for free.
- edge back to `plan` after approval, or to `END`.

This is a faithful 1:1 mapping, not a stretch — the planner/executor split
described in the ticket is already the shape LangGraph assumes by default.

## LangGraph Cloud vs self-hosted
LangGraph Cloud (LangGraph Platform) is a managed deploy target with built-in
horizontal scaling, task queues, and a hosted Postgres — it requires sending
state (including whatever the planner puts in context) to LangChain's
infrastructure. Given instinct 4 (credential/secret handling) and that Lumen
already runs its own Postgres, self-hosted is the only viable option: run
`StateGraph.compile(checkpointer=PostgresSaver(...))` directly inside our own
process (or behind a small FastAPI wrapper), no LangGraph Platform dependency.
Self-hosted loses the hosted dashboard/trace UI; LangSmith tracing is a
separate, optional, also-hosted product — skip it or self-host an OpenTelemetry
collector instead if tracing is wanted later.

## Integration with the Anthropic Claude API
LangGraph is model-agnostic: nodes call whatever LLM client you give them.
`langchain-anthropic` (the official LangChain↔Claude binding) works as a
drop-in model inside a node, or a node can just call the Anthropic SDK directly
and skip the LangChain model-wrapper layer entirely — LangGraph's graph/
checkpoint machinery does not require using LangChain's LLM abstraction at all.
For Limpet, calling the Anthropic SDK directly from the `plan` node (as
`limpet/planner.py` already seems structured to do) avoids pulling in the full
LangChain dependency tree just for the model call.

## Comparison: CrewAI, AutoGen, Prefect
- **CrewAI** — role-based multi-agent framework (agents have named roles/goals,
  coordinate via a "crew"); optimized for fast multi-agent prototyping, not for
  fine-grained resumable state machines. No first-class pause/resume-at-any-node
  primitive comparable to LangGraph's checkpointer. Better fit for a "committee
  of agents debates and converges" pattern than for Limpet's single
  planner-with-tool-calls loop with a hard approval gate.
- **AutoGen (AG2)** — conversation-centric multi-agent framework (agents are
  chat participants exchanging messages in a group chat); good for
  agent-to-agent dialogue and roleplay patterns, weaker on durable
  checkpoint-to-disk semantics than LangGraph. Microsoft's own 2026 comparisons
  position it as "agents talk through problems" vs. LangGraph's "engineering
  blueprint" framing of explicit graph control flow — which matches the
  deterministic pause/resume Limpet needs better.
- **Prefect** — a general workflow/task orchestrator (not agent-specific) with
  mature durable-execution primitives (retries, scheduling, Postgres-backed
  state) — closer in spirit to Temporal/DBOS than to an agent framework.
  Plausible as the *underlying* durable-execution layer if Limpet ever needs
  cron-like scheduling or complex retry policies across many parallel runs, but
  it has no concept of an LLM planning loop, tool-call routing, or
  human-approval interrupt built in — you would still build that logic
  yourself, which is what LangGraph already provides.

Net: for the planner/executor/Sentinel-approval shape in the ticket, LangGraph
is the closest off-the-shelf match of the four. CrewAI/AutoGen solve a
different problem (agent-to-agent conversation); Prefect solves a different
layer (generic durable execution, no agent semantics).

## Estimated complexity to replace linear-chat agents with graph agents
- **Pilot (one flow, one graph)**: 1 Postgres schema/migration for
  `PostgresSaver.setup()`, one `StateGraph` wired to the existing `Planner`
  class and MCP Meta-Server dispatch, one `interrupt()` call wired to Sentinel's
  existing approval mechanism. Rough size: a few days of focused work, mostly
  because `limpet/planner.py` already does the context-assembly LangGraph
  would otherwise want to own (acceptable — compose rather than replace).
- **Fleet-wide migration (every Limpet agent loop)**: every existing node
  boundary (plan/dispatch/wait/approve) needs to become an explicit graph node,
  and anywhere state is implicitly carried in a Python call stack today needs
  to become explicit state-dict fields so a mid-run checkpoint can actually
  resume it. That is a structural rewrite of the executor loop, not a drop-in
  library swap — multi-ticket scope, best estimated after the pilot's
  Sentinel-interrupt integration proves out in practice.

## Fleet state checked
- `langgraph` import already resolves True on this fleet (ISSUE-LOG #655,
  `python3 -c "... find_spec(m) for m in [...,'langgraph',...]"` probe) — no
  install blocker for the pilot.
- `limpet/planner.py:47` (`class Planner`) is the real planner entry point this
  research maps the `plan` node onto; `limpet/test_planner.py` is its existing
  test coverage.
- No existing LangGraph/CrewAI/AutoGen/Prefect research doc predates this one in
  `research/` or `/home/lumen/kb/ISSUE-LOG.md` beyond the dependency-probe and
  the unrelated wq#4317 pico-agent mix-up (ISSUE-LOG row 1455) — this is net-new
  research, not a duplicate.
