# wq#4361 — LIMPET RESEARCH: dissect agent runtime patterns
### OpenAI Agents SDK (parallel guardrails, sandbox abstraction, handoffs) · Agno (tool-result offloading, durable execution, CodeMode) · Pydantic AI (schema-validated outputs, typed DI)

**Sourcing note (instinct 8 — anti-fabrication):** every claim below was pulled from
`fleet-search web` on 2026-10-19 (SearXNG general engines were suspended/CAPTCHA-blocked;
the keyed DeepSeek Search fallback served results), not from memory. Primary sources seen:
- `openai.github.io/openai-agents-python/` / `-js/` — guardrails run in parallel with
  execution and fail fast; agent primitives (agents, handoffs, guardrails, sandbox agents).
- `pypi.org/project/openai-agents/0.18.1` — "Sandbox Agents: Agents preconfigured to work
  with a container to perform work over long time horizons."
- `newreleases.io` mirror of `openai/openai-agents-python` v0.14.0 release notes — the
  sandbox client list (`UnixLocalSandboxClient`, `DockerSandboxClient`, hosted clients for
  Blaxel/Cloudflare/Daytona/E2B/Modal/Runloop/Vercel).
- Temporal's "Introducing Temporal and agentic sandboxes" blog — a worked
  `SandboxClientProvider` registration list, confirming the provider-swap contract.
- `agno.com/articles/community-roundup-august-2026` and `.../what-actually-breaks-agents-in-production`
  — `offload_tool_results`, `ResultStore(threshold_chars=...)`, `AgentFS`, durable
  background execution semantics.
- `github.com/agno-agi/agno` release notes (v3.0.0) and PR #7207/#8210 — `agno_runs`,
  `agno_jobs`, `agno_tool_results` tables; CodeMode pickling into `AgentFS`.
- `github.com/pydantic/pydantic-ai` `docs/output.md` and a raw DBOS durable-exec source
  file — `output_type`, `Agent(retries={'output': N})`, `AgentRetries`.
- `pydantic.dev/docs/ai/core-concepts/dependencies/` and a community PydanticAI-skills
  mirror — `RunContext[T]`, `ctx.deps`, `deps_type=`.

The three-way split (guardrails/sandboxes/handoffs — offloading/durability/CodeMode —
validation-retry/DI/generics) is the ticket's own framing; each claim below is attributed
to the source line it came from, and single-sourced details are flagged.

---

## 1. OpenAI Agents SDK

### 1.1 Parallel guardrails, fail-fast
A guardrail is a check — input validation or a safety/policy check — that runs
**concurrently with agent execution**, not as a gate the agent waits behind. The documented
pattern: run a cheap/fast model as a guardrail in parallel with the expensive model call;
if the guardrail trips (e.g. detects malicious input) it raises and the expensive call is
aborted before it completes. Guardrails attach to both agent **inputs and outputs**.
*(openai-agents-python/-js landing pages, verbatim: "Run input validation and safety checks
in parallel with agent execution, and fail fast when checks do not pass.")*

The engineering shape worth stealing is the concurrency, not just the check: the guardrail
and the main call are dispatched together, and whichever finishes first with a failure
wins — the SDK does not pay full round-trip latency twice (guardrail, then model) for the
common case where the guardrail passes.

### 1.2 Seven-sandbox abstraction for pluggable isolation
"Sandbox Agents" pair an agent with an isolated execution environment — filesystem,
shell commands, file editing, snapshots, session state — behind a single `SandboxClient`
interface. The v0.14.0 release enumerates the concrete clients: `UnixLocalSandboxClient`
(fast local dev, no isolation), `DockerSandboxClient` (container isolation, image parity),
and **hosted clients for seven providers** — Blaxel, Cloudflare, Daytona, E2B, Modal,
Runloop, and Vercel — each an optional extra. Temporal's worked example shows the
contract: a worker registers a `list[SandboxClientProvider]` (name → client instance) and
picks one at run time, so **switching from local Docker to a cloud-hosted sandbox (Daytona,
E2B) is a run-config change, not a code change** — the `SandboxAgent`, its manifest, and
its declared capabilities stay fixed while only the provider swaps.
*(newreleases.io v0.14.0 notes; Temporal blog; developers.openai.com sandbox-guide page:
"Keep the SandboxAgent, manifest, and capabilities stable, then swap the sandbox client and
provider options for the environment you want.")*

### 1.3 Handoffs as first-class delegation
A handoff lets one agent delegate an entire turn to another agent (not a tool call to a
sub-function — a transfer of control), modeled as "agents as tools" in the primitive list
alongside guardrails and sandboxes. The SDK's own framing is deliberately minimal: a very
small set of primitives (agents, handoffs, guardrails, sandbox agents) rather than a large
framework surface. *(openai-agents-python/-js core-concepts pages.)*

---

## 2. Agno — tool-result offloading, durable execution, CodeMode

### 2.1 Tool-result offloading
`offload_tool_results=True` (or a three-state per-member override, per PR #7207) activates
`ResultStore`: any tool return over a configurable `threshold_chars` (default 16,000)
writes to **AgentFS** instead of landing in the model's context. What enters context
instead is a short envelope — a preview, the byte size, and a `result_id` — and the agent
can call `read_result`/`search_result` against that id if it needs the rest. The write path
costs **no model call**: offloading is a deterministic size check, not a summarization
step. *(agno.com community-roundup-august-2026, verbatim: "Anything a tool returns over
16,000 characters goes to AgentFS instead of into context, and the message keeps a short
envelope with a preview, the size, and a result_id.")*

### 2.2 Durable background execution
v3.0 adds a durable background queue: an accepted run is **committed to the database before
it starts**, so a crash or a deploy mid-run doesn't lose it — any replica can pick the run
back up and finish it. Three tables back this: `agno_runs`, `agno_jobs` (the durable queue
itself), and `agno_tool_results` (the offload index from §2.1). *(agno.com
what-actually-breaks-agents-in-production; GitHub v3.0.0 release notes.)*

### 2.3 CodeMode — persistent kernel, state that survives the process
Passing `fs=` to CodeMode makes it pickle every top-level variable independently into
AgentFS after each successful cell execution. A kernel that died, was evicted, or whose
process has since exited **comes back with its variables restored**, and the model is told
in-band what was and wasn't restored (e.g. a `client` holding a `_thread.lock` fails to
unpickle and is reported as not-restored rather than silently dropped).
**Caveat, explicitly stated by the source, not a Limpet inference:** CodeMode is *not a
sandbox* — it runs Python/shell at the same permissions as the host process, and resuming a
session unpickles (= executes) code. The project's own guidance: "trusted operators or an
isolated container, nothing else." *(agno cookbook `03_persistence/basic.py` docstring;
community-roundup-august-2026.)*

---

## 3. Pydantic AI — schema-validated outputs, typed DI, generics

### 3.1 Validation-retry on structured output
`output_type` (a Pydantic model) makes the agent generate a JSON Schema and request
structured output via the provider's native mechanism (OpenAI `response_format`,
Anthropic tool-call interface). The *runtime* validates the raw response against that
schema (type checks, field constraints, enum membership); on failure, **the validation
error is fed back to the model and a regeneration is requested** — not a silent drop, not a
raised exception on the first miss. Retry count is configurable per run:
`Agent(retries={'output': N})`, or an `AgentRetries` dict for finer per-category control
(tool retries vs. output-validation retries are separate budgets, and tool retries cannot
be overridden per run — only configured on the agent). *(cloud.tencent.cn teardown,
corroborated by the raw pydantic_ai DBOS durable-exec source: "Override the agent-level
retry budgets for this run... an int to override the output-validation budget, or an
AgentRetries dict for finer control.")*

### 3.2 Typed dependency injection
Dependencies are injected via `RunContext[T]`, a generic type received as the first
argument by tools, system prompts, and output validators; `ctx.deps` reads the injected
value, and `deps_type=MyDeps` registers the dependency's type on the agent. The project's
stated design philosophy: use existing Python typing idioms rather than inventing new DI
machinery, so dependencies stay type-checked by a normal type checker, not by a
framework-specific validator. *(pydantic.dev/docs/ai/core-concepts/dependencies/;
DougTrajano/pydantic-ai-skills DEPENDENCIES.md mirror.)*

### 3.3 `Agent[DepsT, OutputT]` generic typing
The agent class itself is generic over its dependency type and its output type, so a type
checker can catch a mismatched `ctx.deps` access or a tool/validator returning the wrong
output shape **before** a run ever executes — the type parameters are load-bearing, not
decorative. This is consistent across the `RunContext[T]` tool-call pattern (§3.2) and the
`output_type`-driven schema in §3.1: one generic parameter governs what tools/prompts can
read, the other governs what the run is allowed to produce.

---

## LIMPET AGENT RUNTIME SPEC — what we take, and the contract it sets

Limpet's agent core is still design-only (`design/wq3806_limpet_architecture.md` §6); three
of its pieces already exist as code and map directly onto the patterns above. The spec
below is additive to that §6, not a rewrite of it.

### Guardrail pattern (from OpenAI Agents SDK §1.1)
**Run the critic in parallel with the proposing step it checks, not strictly after it.**
`limpet/critic.py`'s `deterministic_check()` (`limpet/critic.py:81-117`) is already a
guardrail in the Agents-SDK sense — a structural check that can reject a finding before
any model call matters (`limpet/critic.py:26`: "A deterministic structural gate runs
first [...] before any model call would matter"). The gap against the
pattern: today it is sequenced *after* the proposing agent finishes, not dispatched
*alongside* it. The rule this ticket sets for the agent core once it's built: when a hunter
proposes a finding, fire the deterministic gate and the critic-model call concurrently with
whatever the hunter does next (a new hunt subtask, a report draft) — fail-fast cancels
downstream work the instant either the structural gate or the critic model returns
`falsified`/`rejected`, the same way a tripped Agents-SDK guardrail aborts the in-flight
expensive call rather than waiting for it to finish first.

### Result-offloading strategy (from Agno §2.1–2.2)
**Large tool/hunter output goes to the workspace filesystem; only a reference enters the
model's context — this is not new, it is the existing evidence-log contract, now with an
explicit size threshold.** Limpet already treats `evidence/findings/*.json` and
`<workspace>/obligations.json` (`limpet/obligations.py`) as the durable store a finding
cites by ref (`limpet/critic.py:46-48`'s `evidence` vocabulary: a dict with
`path`/`ref`/`id`, or a bare workspace-relative path/REA `ev_` id) rather than inlining
content. The spec addition: adopt Agno's explicit `threshold_chars` rule verbatim — any
tool/hunter output over 16,000 chars writes to the workspace and the context gets an
envelope (preview + size + path/`ev_` ref), never the raw blob — and adopt the durable-queue
discipline (§2.2) for long-running hunters: a hunt subtask is committed to the workspace
*before* it starts (already true, per §6's "each step is a committed workspace change, so
the trajectory is replayable"), so a crashed hunter resumes from the last committed step
instead of re-running from the recon DAG root. Do **not** adopt CodeMode's live-pickle
kernel restore (§2.3) as-is: its own source names the unpickle-on-resume path as
code-execution-on-restore, which is a credential/trust boundary Limpet's workspace isolation
(`design/wq3806_limpet_architecture.md` §5) is built to avoid — if a future ticket wants
persistent-kernel hunters, it must gate kernel-state restore behind the same isolation
boundary a fresh hunter gets, not inherit the host process's permissions.

### Output-validation contract (from Pydantic AI §3.1–3.3)
**Every hunter/agent-core output that crosses a boundary (into the ledger, into a report,
into the next DAG node) is schema-typed, and a schema-validation failure is a feedback
loop, not a crash.** Concretely: a finding is already schema-shaped (`limpet/critic.py:38-44`'s
documented finding dict — `id`/`title`/`severity`/`summary`/`evidence`/`closes`) and
`deterministic_check()` already rejects a structurally invalid finding
(`limpet/critic.py:90-97`) — this is the validation half of the Pydantic AI pattern,
missing only the retry half. The spec addition: when `deterministic_check` rejects a
finding for a fixable structural reason (missing evidence ref, malformed `closes` entry),
return the rejection reasons to the *proposing* hunter for one bounded regeneration attempt
— mirroring `Agent(retries={'output': N})` — before falling through to today's behavior
(reject outright, no ledger obligation opened for a structurally-invalid finding). Model
the agent core's core types the way `Agent[DepsT, OutputT]` does: a hunter step is generic
over its dependency bundle (workspace handle, capability grants, model route from
`limpet/model_router.py:35-51`'s `resolve_model`) and its output type (the finding schema,
or a plain report fragment) — so a type checker catches a hunter wired to the wrong
dependency bundle or returning the wrong output shape before a recon run ever dispatches it,
the same guarantee `RunContext[T]` gives Pydantic AI tools today.

**The one-line design rule this ticket establishes:** *Guardrails run alongside the step
they check and fail fast, not after it; large hunter output lives in the workspace with
only a sized reference in context; every cross-boundary output is schema-validated with one
bounded regeneration retry before outright rejection.*
