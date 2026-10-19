# wq#4368 — LIMPET RESEARCH: dissect enterprise meta-frameworks

Three shipping, sourced enterprise stacks, each answering a different piece of "what does
a *harness* own vs. what does a *custom agent core* own": **Microsoft Agent Framework
(MAF)**'s `Harness` (approval/todo/background-agent/compaction built into the runtime
layer, not the prompt), **Databricks Omnigent** (a meta-harness that sits *above* whatever
harness you already run and adds stateful, session-aware policy), and **Nvidia NemoClaw +
OpenShell** (orchestration blueprints over a kernel-enforced secure runtime that never lets
the agent see a real credential). Limpet's own `limpet/` agent core
(`design/wq3806_limpet_architecture.md` §6, lines 258-309) already hand-rolls pieces of all
three — an obligation gate, a critic pass, an energize-confirm boundary — without a
harness layer underneath any of them.

**Sourcing note (instinct 8 — anti-fabrication):** all three postdate the model cutoff
(Jan 2026; today is 2026-10-19), so every claim below came from `fleet-search
web`/`read` on 2026-10-19 (SearXNG's own engines were suspended/CAPTCHA'd this run —
brave/duckduckgo/google-cse/startpage all reported `Suspended`/`CAPTCHA`; the keyed
DeepSeek-Search fallback carried web discovery). Primary sources fetched directly:
`learn.microsoft.com/en-us/agent-framework/concepts/harness` and
`/agents/planning-and-todos` and `/agents/background-agents` (MAF concept docs),
`learn.microsoft.com/en-us/dotnet/api/microsoft.agents.ai.toolapprovalagent` (the
.NET API reference) plus `github.com/microsoft/agent-framework/pull/5451` (the PR that
introduced `ToolApprovalAgent`); `itbrief.co.nz/story/databricks-launches-open-source-
omnigent-for-ai-agents` and Databricks' own blog post `databricks.com/blog/contextual-
policies-omnigent-using-session-state-better-govern-ai-agents` (full text, byline Matei
Zaharia et al.); `nvidia.com/en-us/ai/nemoclaw/` (product page) and
`raw.githubusercontent.com/NVIDIA/NemoClaw/main/docs/security/gateway-authentication-
controls.mdx` (full doc source, fetched via GitHub raw after the rendered
`docs.nvidia.com` URL returned empty) plus `pypi.org/pypi/openshell/json` (OpenShell's
own PyPI README). All three frameworks are real, currently-shipping products — none of
this is extrapolated from parametric knowledge.

---

## 1. Microsoft Agent Framework — Harness as a first-class runtime layer

**What it is:** `Microsoft.Agents.AI.Harness` (.NET) / `create_harness_agent` (Python), an
"opinionated, batteries-included" layer Microsoft ships *inside* Agent Framework, not a
separate product. The framing in MAF's own docs is exact: *"An agent harness is the
runtime scaffolding that turns a language model into an agent that can perform work. It
drives model and tool calls, manages conversation state and context, applies approval
policies, and can keep the agent progressing through a multi-step task."* Critically, the
harness **composes existing Agent Framework building blocks rather than defining a
separate agent runtime** — a `HarnessAgent` is still a normal `AIAgent`/`Agent`, so
anything written against the base framework keeps working.

- **Capability matrix, enabled by default (MAF's own table):** function invocation with a
  configurable per-request iteration limit, per-service-call history persistence,
  compaction (when token limits or a custom strategy are supplied), **todo tracking**,
  **plan/execute agent modes**, session file memory, **standing tool-approval rules**,
  OpenTelemetry observability, web search (client-dependent), Agent Skills. **Background
  agents** and **shell execution** are opt-in/composed, not on-by-default.
- **TodoProvider — work-item tracking as a context provider, not prompt text.** Two
  context providers back "planning": a todo provider exposing model-facing tools
  (`todos_add`, `todos_complete`, `todos_remove`, `todos_get_remaining`, `todos_get_all`)
  and a separate agent-mode provider that stores the current operating mode and exposes
  tools to read/change it. Either composes standalone, or both ride inside the Harness's
  default pipeline.
- **Plan vs. execute modes — a provider-owned state machine, not a convention.** The
  agent-mode provider is the literal mechanism behind "plan mode" / "execute mode": the
  model calls a tool to *change* its own operating mode, and the harness persists that
  choice in session state so the next turn's system instructions and tool surface reflect
  it. This is architecturally the same move MAF makes everywhere else in the harness: turn
  a thing agents used to negotiate via prose ("I'm now in planning mode, I will not edit
  files") into a tool call with persisted, inspectable state.
- **ToolApprovalAgent — "don't ask again" as middleware, not a system prompt caveat.**
  `ToolApprovalAgent` (introduced in `microsoft/agent-framework` PR #5451) is a
  `DelegatingAIAgent` that sits between caller and inner agent and owns the entire approval
  round-trip: when the inner agent surfaces approval-request content, the middleware checks
  it against persisted `ToolApprovalRule` entries; a match auto-approves, a non-match
  returns one unapproved request to the caller at a time (others queue) until the caller's
  `AlwaysApprove` response records a new rule in the `AgentSessionStateBag` for next time.
  Two rule scopes are supported: tool-level (approve all calls to a named tool regardless
  of arguments) and a narrower argument-scoped form. Rules persist across agent runs within
  a session — the "don't ask again" the ticket names is a first-class, inspectable object,
  not a flag buried in a prompt.
- **BackgroundAgentsProvider — fan-out to named, independently-sessioned children.** A
  parent agent delegates an independent task to a named child agent; each delegated task
  runs concurrently in its **own** child agent session, and the parent holds a task ID it
  can use to wait, retrieve results, continue, or release. MAF's docs are explicit that this
  is distinct from "background responses" (one provider request, polled/resumed) —
  background *agents* invoke a whole separate Agent Framework agent and return only its
  text result to the parent. The feature is flagged **experimental** in MAF's own docs.

---

## 2. Databricks Omnigent — a meta-harness with stateful, session-aware policy

**What it is:** an open-source (Apache 2.0, alpha) **meta-harness** Databricks launched
~June 2026, explicitly *not* a replacement harness: *"It lets you keep using the agent
harnesses you already like, including Claude Code, Codex, and custom agents, while adding
a shared layer for collaboration, composition, and policies."* It sits one level above
whatever harness (MAF's included) an org already runs, and groups its features into three
buckets — **composition** (combine agents/models/frameworks, swap agent systems with
limited code changes), **control** (governance, security, cost), and **collaboration**
(shared live sessions, co-review, real-time steering).

- **Contextual policies — the key primitive, and the piece the ticket names ("stateful
  policies tracking accumulated behavior").** In Databricks' own words: *"Today's agent
  frameworks only have simple controls to limit what an agent can do, e.g., rules to allow,
  deny, or ask the user for various tool calls. But this makes it difficult to create
  policies that are both secure and convenient."* A contextual policy listens to every
  event an agent performs (tool calls/responses, LLM input/output) and can allow, deny,
  transform, or ask-the-user — **and** update arbitrary session-scoped state visible only
  to that policy. The policy author writes one function: `(old_state, new_event) →
  (state_update, decision)`. Omnigent's server owns persisting that state per
  policy-per-session and replays it into the handler on the next event.
- **Three shipped example policies, each keyed to a different kind of accumulated state:**
  1. **No-write-down (Bell-LaPadula).** State = which documents the agent has read this
     session. Writes default to documents the agent itself created; after the agent reads
     a document marked confidential, a subsequent write to a *less-protected* file is
     denied — the classic "no write-down" rule, applied dynamically per session rather than
     as a static ACL.
  2. **Risk score.** State = a single running number. Configured actions each add points
     (a routine tool call: 1-2; reading a highly-confidential document: much more). Below
     threshold, the agent proceeds unprompted; once the score crosses it, risky actions
     (send email, share a file) flip from `ALLOW` to `ASK`. The same email that goes out
     automatically early in a session requires human sign-off later in the *same* session,
     once enough sensitive material has been touched — the policy's whole point is that
     identical actions get different treatment depending on session history.
  3. **Cost policy — the "cost budgeting" the ticket names.** State = cumulative spend on
     model calls this session. A soft threshold pauses and asks whether to continue; a hard
     cap blocks further calls to the *expensive* model specifically, letting the session
     continue on a cheaper model rather than killing it outright. Databricks notes the same
     idea composes upward — a platform team can stack a per-user daily budget on top of the
     per-session one.
- **Framework-neutral by construction, not by convention.** Because policies hook events at
  the Omnigent server (which launches and proxies the underlying agent), the same policy
  applies identically whether the agent underneath is Claude Code, Codex, Antigravity, Pi,
  OpenCode, Hermes, or a custom OpenAI-Agents-SDK/Claude-Agents-SDK build — Omnigent
  intercepts tool calls at the launch boundary rather than requiring each harness to
  implement a policy hook itself.
- **OS-level sandbox + cloud execution, bundled as "control" features alongside the
  policy engine** — Omnigent's announcement groups "cloud execution environments,
  operating system sandboxing" with the contextual-policy and cost-budgeting features
  under the same "control" heading, i.e. the sandbox boundary and the behavioral-policy
  boundary are treated as two instances of the same governance layer, not separate
  concerns owned by different teams.

---

## 3. Nvidia NemoClaw + OpenShell — orchestration blueprints over an agent-blind secure runtime

**What it is:** two separate, co-designed products. **NemoClaw** is "a collection of open
blueprints for building autonomous agents: domain-specialized, always-on AI systems" —
orchestration templates wiring a chosen agent runtime (Hermes, LangChain Deep Agents Code,
OpenClaw/Terminal, custom) to NVIDIA models and tooling. **OpenShell** is the secure
runtime underneath it, shippable and usable independent of NemoClaw (`pypi.org/project/
openshell`, Apache 2.0, alpha): *"the safe, private runtime for fleets of autonomous AI
agents... You declare what each agent can touch in a policy, and OpenShell enforces it."*

- **Orchestration templates and model routing.** NemoClaw blueprints are demonstrated
  against real workflows (Cadence RTL verification, geometry/thermal/injection-molding
  iteration, Blender/Omniverse control) and explicitly combine domain-specialized agents
  with "frontier general agents" under a shared runtime-controls/model-routing/skill-
  execution/observability stack — each blueprint names NVIDIA Agent Toolkit, Nemotron
  models, NeMo (specialization/optimization), and OpenShell (runtime policy) as the four
  composed layers.
- **Nemotron 3 Ultra for long-running agents.** NemoClaw runs LangChain's open-source "Deep
  Agents Code" harness tuned specifically for **NVIDIA Nemotron 3 Ultra** (the ticket's
  "550B" figure is the model's own published parameter count; not independently re-verified
  by me beyond the product page naming the model), "with the flexibility to adapt to other
  models — delivering benchmark-leading accuracy" for the harness's long-running-agent use
  case.
- **CUDA-X Agent Skills — NVIDIA-Verified Agent Skills, confirmed under a slightly
  different but matching name.** The ticket's "CUDA-X Agent Skills" maps onto what NVIDIA
  calls **NVIDIA-Verified Agent Skills** ("NVIDIA Skills"): packaged skill definitions that
  "teach coding agents how to correctly use NVIDIA CUDA-X libraries, NVIDIA Blueprints,
  NVIDIA Omniverse and physical AI workflows, NVIDIA NeMo training and inference tools" —
  a press release from the same launch window states it plainly: *"NVIDIA CUDA-X Libraries
  Available as Skills for Autonomous Agents."*
- **OpenShell's core move: the agent never sees a real credential.** Quoting OpenShell's
  own README: *"Agents never see real credentials; OpenShell adds them only to requests
  bound for approved endpoints."* This is enforced two ways — **kernel-level**: each agent
  runs in an isolated sandbox where kernel controls confine file access and syscalls, and
  every outbound network connection passes a policy check before leaving the sandbox;
  **formal verification**: a proposed policy change is checked *before* approval for newly
  granted risky access (a new host reachable with credentials, a new API method), routing
  genuinely risky changes to human review instead of silently widening scope.
- **CLI Secret Redaction — always-on, not a toggle.** NemoClaw's CLI "automatically
  redacts secret patterns (API keys, bearer tokens, provider credentials) from command
  output and error messages before logging them," on stdout/stderr/thrown-error paths,
  with no user-facing disable switch (`docs/security/gateway-authentication-controls.mdx`,
  "CLI Secret Redaction" section).
- **Memory Secret Scanner — a preventive, pre-write gate, the single most load-bearing
  comparison point for this ticket's masking-spec ask.** A `before_tool_call` hook
  intercepts `Write`/`Edit`/similar calls targeting memory and workspace paths and scans
  for **14 high-confidence secret patterns** (API keys, tokens, private keys) *before*
  anything reaches disk; a blocked write returns the agent an actionable error listing the
  detected patterns, rather than silently stripping or allowing it through. The doc is
  explicit that this is "not a user-facing knob" — always on, same posture as OpenShell's
  credential-blind sandbox above it. Two other agent-runtime variants in the same doc
  (Hermes, LangChain Deep Agents) don't carry this specific plugin and are told instead to
  keep secrets in OpenShell providers / environment variables and avoid writing raw
  credentials into their own state files — the scanner is this one plugin's belt, not the
  system's only suspender.

---

## Evaluation: MAF's harness pattern vs. building custom for Limpet

Limpet's agent core today (`design/wq3806_limpet_architecture.md` §6) is a hand-rolled
stack sitting directly on a local LLM with no harness layer underneath: `limpet/
obligations.py` (an unanswered-question ledger gating "done"), `limpet/critic.py` (a
propose/critic/adjudicate pass before a finding commits), and the energize-confirm
boundary (an unconditional hardware-action stop, §4) are each a bespoke module wired into
the investigation loop by hand. None of MAF's four building blocks — todo tracking,
plan/execute mode, tool approval, background-agent fan-out — exist in Limpet today, and
three of the four map onto work Limpet's design doc already calls "deferred" or "future":

- **Todo tracking** is the closest overlap, and Limpet's version is *stronger* in one
  specific way MAF's isn't: `obligations.py`'s ledger is a hard gate (`obligations
  verify`, exit 3, refuses "done" with any item open) backed by a cited-evidence
  requirement, where MAF's `todos_add`/`todos_complete` tools are plain state the model
  manages voluntarily with no verify-step enforcement described in the docs fetched. **Do
  not replace** obligations.py with MAF's TodoProvider — it would be a downgrade.
- **Plan/execute mode** is a real gap: Limpet's loop (`identify → choose toolset →
  orchestrate → verify → report`) is currently a fixed sequence with no explicit
  "I am planning, not yet acting" state the agent can declare and have enforced (e.g. no
  energizing tool calls while in a plan-only mode). MAF's `AgentModeProvider` pattern — a
  tool-settable, harness-persisted mode that the tool surface itself conditions on — is a
  clean, small primitive Limpet does not have and plausibly should: it would let the
  energize-confirm boundary (§6, line 294) be enforced structurally (the harness refuses an
  energizing tool call while mode=plan) rather than only as a documented policy the agent
  core is trusted to honor.
- **Tool approval with "don't ask again" rules** does not fit Limpet's shape at all.
  Limpet's autonomy boundary (§6, lines 290-302) is binary and intentionally non-
  negotiable for one category (energize-confirm is "policy... not a per-agent or
  per-session judgment call" regardless of trust tier) and otherwise fully autonomous
  (tool orchestration is unrestricted). MAF's `ToolApprovalAgent` solves a *different*
  problem — a human in the loop approving individual tool calls with memoryable exceptions
  — that Limpet's single-appliance, operator-confirms-hardware-only model doesn't have a
  slot for. Building this would be solving a problem Limpet doesn't have.
- **Background-agent fan-out** is marked experimental even in MAF itself, and Limpet is
  explicitly one investigation per workspace session (same conclusion reached independently
  in `research/WQ4367_ORCHESTRATION_DISSECTION.md`'s evaluation of OpenAI Symphony) — there
  is no multi-ticket backlog to fan child agents out against. Skip it.

**Recommendation: adopt the plan/execute mode *pattern*, not the MAF package.** Limpet has
no .NET/Python Agent Framework dependency and no reason to take one on for a 16GB-RAM
on-box appliance (`design/wq3806_limpet_architecture.md` §6, lines 260-264) — pulling in
MAF wholesale to get one provider would be adopting a foreign runtime for a feature that is
~50 lines of session state plus a tool-surface conditional. The actually load-bearing
lesson from MAF is structural, not a dependency: **separate "the agent declared it is
planning" (a tool call, persisted state) from "the harness enforces that declaration" (the
tool surface itself, not agent discipline)** — and wire that distinction into the existing
energize-confirm boundary so a plan-mode agent is *structurally* unable to call an
energizing tool, rather than trusted not to. `obligations.py` and `critic.py` stay
exactly as they are; they already outperform MAF's equivalent primitives for Limpet's
single-session, verify-gated shape.

---

## Sensitive-data masking spec for RE artifacts

**The gap this closes.** Limpet's two existing evidence-path gates handle two specific
threats and neither handles this one: `limpet/sanitize.py` (`sanitize_workspace`, line 201)
redacts prompt-*injection phrasing* in recovered strings before the first agent read,
quarantining the original under `<workspace>/quarantine/` (`QUARANTINE_RELPATH`, line 66);
`limpet/binary_scrub.py` strips control characters and invisible Unicode from the same
recovered strings. Neither scans for **credential-shaped content** — and a firmware image
or app binary is exactly the kind of artifact that embeds real secrets (hardcoded vendor
API keys, device-provisioning tokens, private keys baked into a default config, a
developer's leaked cloud key in a debug string) that Limpet's own evidence pipeline will
happily extract into `evidence/re-exports/*.json` and `evidence/**/*.txt`, display to the
agent, and potentially echo into a finding, report, or exported evidence bundle. Lumen's
own `harness/masking.py` (`PATTERNS`, line 452) is the house credential-pattern set, but it
is built for a different threat model: it masks *Lumen's own* credentials in fworker's
artifacts **after** the model has already seen them — "a hit is an ALARM, not a save...
the token already reached the model and is burned" (`harness/masking.py:8-12`), because
there is no lever to intercept a tool result before the model's context (CLI 2.1.233 has
no `updatedToolResult`/`replaceOutput` hook). That post-hoc, rotate-same-day posture is
correct for Lumen's *own* secrets. It is the wrong posture for a *target's* secret found
during RE: there is nothing to rotate (Limpet doesn't own the vendor's key), and the actual
risk is narrower but still real — that secret must not leak into a committed finding, an
exported evidence bundle, or a report shared outside the workspace.

**Spec — extend `sanitize.py`'s existing pre-read gate, don't bolt on a second one:**

1. **Reuse `harness/masking.py:452`'s `PATTERNS` list as the credential classifier**,
   imported the same way `sanitize.py` already imports `injection.classify` — one source
   of truth for "what does a credential look like," already instinct-4-maintained in
   lockstep with `/home/lumen/bin/mask-secrets`. Do not hand-roll a second pattern set for
   RE evidence; a drifted copy is exactly the failure mode instinct 4's "keep the two in
   lockstep" note already warns about for the existing two copies.
2. **Classify, don't alarm.** A credential-pattern hit inside `sanitize_workspace`'s walk
   (`limpet/sanitize.py:80`, `_sanitize_tree`) is not an ALARM event (no rotation ticket —
   it isn't Lumen's secret) — it is redacted into the quarantine file the same way an
   injection-phrasing hit already is (`_quarantine_entry`, line 76), with a `layer` tag of
   `"credential"` alongside the existing `"injection"` tag, so the ledger distinguishes the
   two without a second file format.
3. **Preventive, not post-hoc — this is the one place NemoClaw's Memory Secret Scanner
   and OpenShell's credential-blind gateway are the closer model than Lumen's own
   `masking.py`.** Because `sanitize_workspace` already runs *before* the first agent read
   (that is the whole point of the gate — line 6-7 of the module docstring), a credential
   hit here is caught before the model ever sees it, matching NemoClaw's `before_tool_call`
   interception on `Write`/`Edit` (gateway-authentication-controls.mdx, "Memory Secret
   Scanner") and OpenShell's "agents never see real credentials" posture — not Lumen's own
   masking.py, which by its own docstring (line 8) structurally cannot do this for
   Lumen's own CLI stream and settles for a same-day-rotate alarm instead. RE evidence
   doesn't have that constraint: nothing stops Limpet from gating it before the first read,
   so it should.
4. **The quarantined original stays recoverable evidence, never deleted.** Same posture
   `sanitize.py` already takes for injection hits (module docstring, line 7: "nothing is
   destroyed, only kept out of context") — a real embedded vendor credential is itself a
   finding (default/hardcoded-credential is a standard RE vulnerability class), so the
   redaction must not erase the evidence that it existed; it must only keep the live value
   out of the model's context and out of anything exported.
5. **Export/report path gets the same scan as a second, independent check.** `critic.py`'s
   propose/critic/adjudicate step (`design/wq3806_limpet_architecture.md:281-289`) already
   runs before a finding commits — add the same `PATTERNS` scan as a deterministic check
   there (alongside its existing "no cited evidence" / "closes an open obligation" checks),
   so a credential that entered context through a path `sanitize_workspace` doesn't cover
   (a tool's live stdout during static analysis, not a file under `evidence/`) is still
   caught before it reaches a committed finding or an exported bundle. This mirrors
   OpenShell's two-layer design (kernel enforcement *and* formal verification of policy
   changes) rather than relying on the workspace-file gate alone.

This keeps Limpet's existing module boundaries intact (`sanitize.py` owns the pre-read
gate, `critic.py` owns the pre-commit gate, `masking.py` owns the canonical pattern set)
and borrows the one structural idea the three enterprise stacks converge on that Limpet's
own masking.py by construction cannot do for its own use case: catch the secret **before**
it reaches the model, not after.
