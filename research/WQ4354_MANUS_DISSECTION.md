# wq#4354 — LIMPET RESEARCH: dissect Manus

**What Manus is:** a general-purpose autonomous execution agent built by Monica.im,
public since **2025-03-05**. The user states a goal in natural language; Manus plans,
then drives a real cloud computer — browser, filesystem, terminal, software tools — to
carry the goal out end to end, continuing to run after the user disconnects and
returning only for approval on sensitive steps. Acquired by Meta in 2026 for a reported
**$2–3B**; it is the architectural blueprint Meta Muse (wq#4335) replicates. The
defining property, repeated across every source, is that **Manus is not a single LLM
agent — it is a coordination system**: a planner decomposes the goal, executor agents
carry out the pieces, each inside its own isolated virtual computer.

**Sourcing note (instinct 8 — anti-fabrication):** every architectural claim below was
pulled from web search on 2026-10-19 (SearXNG general engines were all
suspended/CAPTCHA-blocked, so the keyed DeepSeek Search fallback served results), not
from memory. Primary sources seen:
- E2B customer writeup "How Manus Uses E2B to Provide Agents With Virtual Computers"
  (e2b.dev) — confirms planner→executor decomposition and E2B as the sandbox substrate.
- ZenML LLMOps database "Building an AI Agent Platform with Cloud-Based Virtual Machines
  and Extended Context" — the "less structure / context over code" philosophy, private
  data + paid-API pre-integration, consumer targeting.
- DEV Community "Manus gives every task its own cloud computer" — summarizes Manus's
  **January 2026 Manus Sandbox** post: one fully isolated VM per task, with networking,
  filesystem, browser, tools; files survive sandbox sleep/wake.
- emergentmind "Manus AI: Autonomous Mind and Hand Integration" — cites Shen et al.
  (4 May 2025), names the three principal agent types (Planner / Executor / …).
- A long Chinese teardown (cloud.tencent.cn) — claims the planner explores the
  decomposition space with an **MCTS**-style search over cost/risk before committing a
  plan. *Treat the MCTS detail as single-source;* the planner→subtask-sequence pattern
  itself is corroborated by E2B and emergentmind.

The "$2–3B acquisition" and the six numbered dissection targets are the **ticket's own
framing**; I confirmed the architecture (multi-agent, per-task VM, decomposition,
sandboxed execution, approval gate) against the sources above and flag below where a
claim rests on only one of them.

---

## Architecture diagram

```
        ┌──────────────────────────── USER (phone / web) ────────────────────────────┐
        │  NL goal in; approval prompts out; result/artifact view. Holds no agent.    │
        └───────────────▲──────────────────────────────────────────────┬─────────────┘
                        │ approval requests                             │ goal
        ┌───────────────┴──────────────────────────────────────────────▼─────────────┐
        │  MULTI-LAYER AGENT STACK  (each layer separately replaceable)                │
        │                                                                              │
        │  (1) INTENT + PLANNING ──── decomposes NL goal into a SUBTASK DAG            │
        │          │  nodes = executable subtasks; edges = data/order deps             │
        │          ▼                                                                   │
        │  (2) ORCHESTRATION ─── schedules ready DAG nodes onto executors              │
        │          │                                                                   │
        │   ┌──────┼───────────────┬───────────────────┐                              │
        │   ▼      ▼               ▼                   ▼                               │
        │  (3) BROWSER/DOM   (4) CODE EXEC      (5) TOOL BUS      (6) LONG-TERM        │
        │   hybrid visual     sandboxed          multimodal        MEMORY             │
        │   (pixel) + DOM     (WASM/microVM)     file/API/search   (context, not      │
        │   localization                                           fine-tune)         │
        └──────────┬───────────────────────────────────────────────────────┬─────────┘
                   │ one leaf subtask                                        │
        ┌──────────▼───────────────────────────────────────────────────────▼─────────┐
        │  CLOUD COMPUTER — one fully isolated VM PER TASK (E2B substrate)             │
        │  networking · filesystem · browser · terminal · installed tools             │
        │  survives sleep/wake (files persist); agent keeps running while user offline │
        │  APPROVAL GATE intercepts sensitive local commands before they run          │
        └──────────────────────────────────────────────────────────────────────────┘
```

---

## Dissection

### 1. Cloud Computer — one VM per task
Every task gets its **own** fully isolated cloud VM, not a shared pool. The VM is a
real personal-computer environment: networking, a filesystem, a browser, a terminal,
installed software. The sandbox can **sleep and wake with files intact**, which is what
lets the agent run long after the user closes the app and resume cleanly. Manus buys
this from **E2B** rather than building VM orchestration itself — E2B's pitch is "run
untrusted agent code securely and at scale," which is exactly the per-task-VM primitive.
*(E2B writeup; DEV Community summary of the Jan-2026 Manus Sandbox post.)*

### 2. Multi-layer agent stack (separately replaceable)
Six conceptual layers: (a) intent understanding + planning, (b) browser/DOM
manipulation, (c) sandboxed code execution, (d) long-term memory, (e) multimodal tool
bus, (f) orchestration between them. The sources confirm the planner/executor split and
the tool diversity (browser, file search, terminal); the strict six-way layering is the
ticket's taxonomy, consistent with but finer-grained than any single source. The
engineering point that survives: **layers talk through narrow contracts, so each is
swappable** (swap the planner model, swap the browser driver, swap the sandbox provider)
without touching the others.

### 3. Subtask-graph decomposition — a DAG, not a prompt chain  ← the substrate we want
The planner turns the NL goal into a **graph of executable subtasks with explicit
dependencies**, then the orchestrator runs nodes as their inputs become ready. This is
categorically different from a flat prompt chain (step→step→step in one loop): a DAG
lets independent subtasks run in parallel, lets a failed node be retried/replanned
**without rerunning its siblings**, and makes the plan an inspectable artifact rather
than hidden loop state. One source (the Chinese teardown) claims the planner uses an
**MCTS**-style search over candidate decompositions, scoring cost and risk before
committing — single-sourced, but the direction (plan is *searched and chosen*, not
greedily emitted) is the load-bearing idea for us.

### 4. Hybrid visual + DOM interaction
The browser layer combines a **pixel-level screenshot** (what a human sees, robust to
canvas/custom widgets) with **source-level DOM element localization** (stable
selectors, exact text, precise click targets). Pixels handle what the DOM can't express;
the DOM handles what pixels can't address reliably. This is the same two-channel pattern
our own computer-use executor already leans toward (wq#4337 dissection, §coordinate-space
contract): coordinates live in the executor, not the model's head.

### 5. Sandboxed code execution
Untrusted code runs inside the per-task VM under **microVM / WASM-class isolation**
(E2B is Firecracker-microVM-based), so an executor compromising its own box cannot reach
the host, the orchestrator, or other tasks' VMs. Isolation is **per task**, which means
the blast radius of a bad subtask is one leaf.

### 6. Approval gate for local commands
Sensitive local commands are **intercepted and held for user approval** before they run
— the agent is autonomous up to a boundary, then fails to a human. This is the same
shape as our two-model/judge gate (wq#4337 §two-model gate): a narrow allowlist runs
free, everything else routes to a decision that can block.

---

## LIMPET VALUE — what we steal, and the design rule it sets

**The planning substrate for `recon → hunters → reconcile` is a subtask DAG, not a
single loop.** Today recon is implied to be one flow; Manus is the proof that a
general-purpose agent that actually completes multi-step real-world work models the plan
as a **directed acyclic graph of subtasks** and schedules leaves onto isolated workers.
Map it directly:

1. **Recon phase = graph planner.** Recon's job is not "do the first step"; it is to
   emit a DAG — nodes are hunt subtasks, edges are data/order dependencies — and hand it
   to an orchestrator. Make the DAG an inspectable artifact (serializable, loggable,
   replayable), the way Manus's plan is a first-class object. Prefer *searching* a few
   candidate decompositions and scoring cost/risk (Manus's MCTS direction) over greedily
   emitting the first chain that comes to mind.

2. **Each hunter = a leaf with an isolated workspace.** One hunter subtask gets one
   isolated workspace (our analogue of Manus's per-task VM): its own filesystem/scratch,
   its own tool grants, no reach into a sibling hunter's state. Blast radius of a bad
   hunter is one leaf. This is already the direction of limpet's workspace isolation
   (`limpet/workspace_index.py`, `limpet/workspace_console.py`); the ticket's demand is
   to make it the *default per-leaf*, not an option.

3. **Reconcile = DAG join nodes.** Reconcile is not a post-hoc pass; it is the set of
   join nodes where multiple hunters' outputs are dependencies of a downstream subtask.
   Model it in the graph, don't bolt it on after the loop.

4. **Independent hunters run in parallel; a failed leaf replans locally.** The whole
   reason to pay for a DAG over a loop: siblings with no edge between them run
   concurrently, and a failed leaf is retried or replanned **without rerunning the rest
   of the graph**. A flat loop forces re-execution and serializes work that had no
   dependency.

5. **Layer the stack behind narrow contracts.** Keep planner / workspace-driver /
   sandbox-provider / memory as separately replaceable layers, so we can swap the recon
   planner model or the hunter sandbox without touching the rest.

6. **Keep the approval gate on local commands.** A hunter that wants to run a sensitive
   local command routes through the existing judge/gate (wq#4337 §two-model gate), not
   straight to exec — autonomous to a boundary, then fail to a human.

**The one-line design rule this ticket establishes:** *Do not flatten recon into a
single loop. The recon planner emits a subtask DAG; each hunter is a leaf with an
isolated workspace; reconcile is the graph's join nodes.*
