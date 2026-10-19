# wq#4365 — Dissection: visual workflow builders (Dify, Sim) vs the Limpet Target Device Wizard

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Sources:** Dify's own docs and code
mirrors (`deepwiki.com/langgenius/dify`, `langgenius/dify` DSL/skill repos, `langgenius/dify`
`LICENSE`), Sim Studio's docs and repo (`sim.ai`/`simstudio.ai` library posts, `simstudioai/sim`
PRs #2953/#6412/#6413 and `deepwiki.com/simstudioai/sim`), all retrieved 2026-10-19 via
`fleet-search web`. **SearXNG general engines were suspended/CAPTCHA'd fleet-wide again** (brave
"too many requests", duckduckgo timeout, google cse suspended, startpage CAPTCHA); the keyed
DeepSeek Search fallback served every query — same outage pattern on record for the sibling
LIMPET dissections (wq#4347/wq#4356/wq#4358). **Scope honesty:** this is a documentation- and
community-source dissection, not a read of either source tree — `WebFetch`/`git clone` are not
on this worker's allowlist. Vendor behaviour below is the vendors' own documented framing and
may be aspirational in places; the Limpet-side claims are read directly from this repo and are
cited to file:line.

Companion to the sibling LIMPET build-vs-buy dissections —
`research/WQ4357_LANGGRAPH_DISSECTION.md` (graph state machines),
`research/WQ4358_CREWAI_DISSECTION.md` (role delegation), and
`research/WQ4356_A2A_PROTOCOL_DISSECTION.md` (cross-agent wire contract). Those examined
orchestration *engines*; this one examines orchestration **author-time surfaces** — the
drag-and-drop canvas — against Limpet's already-specified configuration surface: the headless
Target Device Wizard (`docs/target-wizard-spec.md`) and the Textual TUI agent chat
(`DESIGN-DECISIONS.md`, `limpet/workspace_console.py`).

---

## 0. The question, stated so it can be answered

The ticket's OUTPUT is a decision: **does Limpet need a visual workflow builder for the Target
Device Wizard configuration surface, or is the Textual TUI agent chat sufficient?** Two products
are offered as references:

1. **Dify** — visual multi-step workflow builder, typed variable passing between nodes, MCP
   integration, self-hostable with Ollama/LiteLLM. Cited as a reference for *non-developer
   analyst wizard configuration*.
2. **Sim** (Sim Studio, `simstudioai/sim`) — deploy a workflow *as* an MCP server. Cited as a
   pattern for exposing the Target Device Wizard as an MCP tool other agents invoke.

Answering it requires knowing (a) what each product actually is, (b) what the Limpet wizard
*already is*, and (c) whether the wizard is even the kind of thing a visual builder builds.

## 1. Dify — what it actually is

Dify (LangGenius, first release 2023-04) is an open-source LLM-app *development platform*. A
builder visually composes AI workflows, RAG pipelines, and tool-using agents on a canvas and
ships them as hosted apps, embedded APIs, or MCP servers from one self-hostable Docker stack.

- **Node-based DAG engine.** A workflow is a directed acyclic graph; nodes are operations
  (LLM call, Knowledge Retrieval, Agent, Tool, IF-ELSE, Iteration, Template, Classifier,
  Parameter-Extractor, HTTP-Request, Code, Start/End) and edges define data flow. A node factory
  with dependency injection instantiates each; execution is tracked and state is persisted.
- **Typed variable passing** is the headline feature. The Start node collects typed user input
  (text / paragraph / dropdown / number / single-file / file-list). Each node declares input
  variables (from upstream nodes or `sys.*` system variables via a `variable_selector`),
  configuration, and typed output variables; downstream nodes reference them. Per-node error
  branches and retry/exponential-backoff are first-class.
- **App modes:** `workflow` (one-shot/batch/triggered), `advanced-chat` (multi-turn Chatflow
  with `sys.query`/`sys.files`/conversation vars), plus legacy `chat`/`completion`/`agent-chat`
  and a portable top-level `agent` (0.7.0). The Agent node runs Function-Calling or ReAct
  bounded by a Maximum-Iterations cap.
- **Self-host** via Docker Compose / Helm / K8s; model providers (incl. Ollama, and any
  OpenAI-compatible gateway such as LiteLLM) are configured in System Settings. The DSL is YAML,
  exportable/importable. Dify can itself publish an app as an MCP server.
- **License — the ticket overstates this.** Dify is **not** plain Apache-2.0. Its `LICENSE` is a
  *modified Apache-2.0 with additional commercial conditions*: you may not operate a multi-tenant
  service on the source without a written commercial license, and you may not remove Dify's
  logo/copyright from the console UI. For Limpet's single-tenant, re-skinned appliance use this
  would almost certainly require a commercial license or a hard fork of the frontend — a material
  build-vs-buy fact the "55K stars Apache-2.0" framing hides.

## 2. Sim — what it actually is

Sim Studio (`simstudioai/sim`) is an open-source AI-agent workflow builder, self-hosted via
`npx simstudio` or Docker Compose, with Ollama local-model support. Its distinctive pattern
(v0.5) is **workflow-as-MCP-server**:

- Deploy any workflow, then **separately** expose it as an MCP server. It becomes a callable tool
  for any MCP-compatible agent (Claude Desktop, Cursor, custom apps).
- **The tool contract is derived, not authored.** Sim generates a tool definition from the
  workflow: the name/description you give, plus a **JSON schema derived from the Start block's
  input format**. Transport is **Streamable HTTP** on a single URL; auth via API-key headers or
  public access. The MCP server API (`/api/mcp/serve/[serverId]`) is a JSON-RPC interface;
  tool lists are discovered dynamically. An A2A layer sits on top for agent-to-agent tasking.
- The Agent block can call *other workflows as tools* (hierarchical coordinator → sub-workflow),
  query knowledge bases, and run custom JS/Python in sandboxes. The workflow-as-tool/MCP bridge
  runs in-process (`simstudioai/sim` #6412/#6413).
- License: Apache-2.0 core (as the ticket states).

**The useful idea here is the contract, not the canvas:** expose a fixed capability as an MCP
tool whose input schema is derived from its entry point, so logic lives in one place and every
assistant supplies only the conversation. That is a *pattern*, and it is transport/schema plumbing
— nothing about it requires Sim's drag-and-drop editor.

## 3. What the Limpet wizard already is

The Target Device Wizard (`docs/target-wizard-spec.md`, wq#3901) is **already specified**, and
its first constraint directly pre-empts a visual builder:

- **Headless is the primary mode (spec §0.1).** Limpet has no attached console (fleet instinct
  1); it is driven by SSH/MCP from other nodes. The wizard is "a **headless state machine with a
  thin GUI on top**, not a GUI with a scripting bolt-on. The GUI and the CLI/MCP driver call the
  *same* engine; the GUI holds no logic the headless path lacks." A browser-canvas builder
  (Dify/Sim both serve a web UI at `localhost:3000`) is exactly the inverted shape the spec
  rejects.
- **It orchestrates a fixed flow, it does not author one (spec §4).** The state machine is a
  single onboarding pipeline — detect → identify (VID:PID → vision fallback) → confirm with
  confidence/evidence → create isolated workspace → bind hardware → queue first analysis step.
  Entry points are `limpet target new` (CLI) and the `target_new` MCP tool; sessions persist to
  `/var/limpet/sessions/<id>.json` so an interrupt resumes.
- **The operator surface is the agent chat**, not a menu or a canvas: the TUI agent chat drives
  the same core as the CLI (e.g. `/export` → `workspace_report`), and the deployment UX
  deliberately "surfaces tools through a searchable agent index rather than a 600-entry menu…
  first boot is a provisioner, not a wizard" (`design/wq4060_limpet_deployment_distro_ux.md`).

So Limpet already has: a typed, persisted wizard state machine; one engine behind CLI/MCP/GUI;
an MCP meta-server (wq#3808) that exposes tools to other agents; and a conversational TUI as the
human surface. The two reference products overlap this heavily — and where they don't, they
conflict with instinct 1.

## 4. Decision

**No. Limpet does not need a visual workflow builder for the Target Device Wizard. The Textual
TUI agent chat, over the already-specified headless wizard state machine, is sufficient.** Adopt
two *patterns* from the references; adopt neither product.

**Why no builder:**

1. **Wrong shape for a headless box.** Dify and Sim are browser-canvas editors whose logic lives
   in the GUI. The wizard spec's load-bearing constraint (§0.1) is the exact opposite: headless
   engine is the source of truth, any GUI is thin. A visual builder would re-introduce the
   GUI-with-a-scripting-bolt-on the design already rejected.
2. **Nothing to author.** A visual builder's value is letting a non-developer *compose arbitrary*
   workflows from typed nodes. The wizard is *one fixed flow* — Limpet's own onboarding pipeline,
   versioned in code and in `target-devices.d/*.toml`, not a graph an analyst redraws per device.
   The "non-developer analyst" does not want to wire a DAG to plug in a device; they want "plug
   it in → tell me what it is → start," which is a guided linear flow plus chat. There is no
   authoring surface for a builder to fill, so the build-vs-buy question has no "buy" side here.
3. **License and weight.** Dify's modified-Apache license would likely force a commercial license
   or a frontend fork for Limpet's re-skinned single-tenant appliance; both products drag a full
   Docker/web stack onto a box whose whole premise is headless SSH/MCP operation.

**What to borrow (patterns, not products):**

- **From Sim — the workflow-as-MCP-server contract.** This is the ticket's genuinely good idea
  and Limpet is already most of the way there: the wizard's `target_new` MCP tool is the entry
  point. Formalize it the way Sim does — a JSON-schema tool contract *derived from* the wizard's
  typed entry inputs, served over the existing MCP meta-server so other fleet agents can invoke
  "onboard this device" as a tool. Pattern adopted; Sim the product not installed.
- **From Dify — typed node-I/O discipline.** Dify's typed variable passing is the right rigor for
  the wizard's internal step contracts (each step declares typed inputs/outputs, carries
  confidence/evidence, persists to the session JSON). Borrow the discipline for the state
  machine's step schema; do not adopt the canvas that makes it editable at runtime.

**Net:** the configuration surface Limpet needs is (a) the TUI agent chat for humans and (b) the
MCP tool contract for agents — both of which the architecture already names. A visual workflow
builder adds an author-time surface Limpet has no use for and a GUI topology its headless
instinct forbids.

## 5. Caveats

- Documentation/community-source dissection, not a source read (`WebFetch`/clone off-allowlist).
  Vendor capabilities are the vendors' own claims. The Dify license terms and Sim MCP-server
  behaviour were read from the vendors' own `LICENSE`/docs and are the most load-bearing external
  facts; both were cross-checked across ≥2 sources this session.
- Limpet-side claims (headless constraint, fixed flow, `target_new` MCP tool, TUI-as-surface) are
  read from this repo and cited in §3; they are design specs (`docs/target-wizard-spec.md` is
  explicitly design-only), so "already is" means "already specified," not "already shipped."
