# wq#4374 — Adversarial verify-pass of wq#4361 (PR#2252: dissect agent runtime patterns)

**Verdict: CONFIRM** — `research/WQ4361_AGENT_RUNTIME_PATTERNS_DISSECTION.md` (203 lines,
PR#2252, branch tip `bcd3366a`) holds up under independent re-sourcing of every external
claim and direct re-check of every local-repo citation. One local citation has drifted by
~12 lines on current `main` due to an unrelated, later-merged ticket inserting content
above the cited function — not an author error, detailed below as a caveat, not a finding.

## What was shipped

PR#2252 added `research/WQ4361_AGENT_RUNTIME_PATTERNS_DISSECTION.md`, a desk-research
dissect of three agent-runtime projects (OpenAI Agents SDK: parallel guardrails, sandbox
abstraction, handoffs; Agno: tool-result offloading, durable execution, CodeMode; Pydantic
AI: schema-validated outputs, typed DI/generics) plus a "LIMPET AGENT RUNTIME SPEC" section
mapping each pattern onto Limpet's existing `critic.py`/`obligations.py`/`model_router.py`
and the still-design-only agent core (`design/wq3806_limpet_architecture.md` §6).

## Probe

Research doc, not code — same probe shape as precedent (wq#4366/IronClaw): (1) re-derive
every external factual claim from fresh, independent `fleet-search web` queries rather than
trust the doc's own citations, (2) re-check every local file:line citation against the
actual files in this worktree, (3) adversarially test the one framing claim that could be
stale ("Limpet's agent core is still design-only").

**Local citations — checked directly:**
- `limpet/critic.py:26` ("A deterministic structural gate runs first... before any model
  call would matter") — confirmed verbatim.
- `limpet/critic.py:38-44` (finding dict shape: `id`/`title`/`severity`/`summary`/`evidence`/`closes`)
  — confirmed verbatim.
- `limpet/critic.py:46-48` (evidence vocabulary: dict `path`/`ref`/`id`, or bare `ev_` id /
  path#locator string) — confirmed verbatim.
- `limpet/critic.py:81-117` (`deterministic_check`) and `:90-97` (structural rejection path)
  — confirmed; function body matches exactly, including the no-evidence-entry and
  open-obligation-blocked checks the doc describes.
- `limpet/critic.py:120-154` (`review()` calling `deterministic_check` before consulting
  `critic_verdict`) — confirmed the gate runs strictly before the model verdict is
  consulted, matching the doc's claim that the gap is "sequenced after the proposing agent
  finishes, not dispatched alongside it."
- `limpet/obligations.py:25,44,227` (evidence-ref vocabulary: `ev_<64 hex>` regex, workspace
  path, waive-reason-or-path language) — confirmed, consistent with the doc's claim that
  `critic.py` reuses the same vocabulary `obligations.py` already accepts.
- `design/wq3806_limpet_architecture.md` §6 ("Agent core", heading present) and its exact
  phrase "Each step is a committed workspace change, so the trajectory is replayable"
  (line 273) — confirmed verbatim.
- No `agent`/`orchestrator`/`hunter` module exists under `limpet/` — confirms the doc's
  framing that the agent core is still design-only, not stale.

**One citation that drifted (not an author error):**
- `limpet/model_router.py:35-51` for `resolve_model` — on current `main`, `resolve_model`
  starts at line 47, not 35 (lines 35-46 are now a `REQUIREMENT_PROFILES`/`RoutingError`
  block). Checked whether this is fabrication: `git show bcd3366a:limpet/model_router.py`
  (the PR branch's own snapshot) has `resolve_model` starting at line 35 — the citation was
  exactly correct against the file as it existed when the PR was authored.
  `git merge-base --is-ancestor 8b8af83b bcd3366a` → false: commit `8b8af83b`
  ("wq4355 dissect perplexity computer routing", which inserted the
  `REQUIREMENT_PROFILES` block) landed on `main` after PR#2252's branch point, pushing the
  function down ~12 lines. This is ordinary concurrent-ticket citation drift, not a
  fabricated or sloppy citation — recorded as a caveat for whoever next edits
  `model_router.py` near that line range, not a contradiction.

**External citations — re-sourced fresh via `fleet-search web`, not trusted from the doc:**
- OpenAI Agents SDK: "Run input validation and safety checks in parallel with agent
  execution, and fail fast when checks do not pass" — confirmed verbatim against
  `developers.openai.com/api/docs/guides/agents/guardrails-approvals`.
- Sandbox client list: `UnixLocalSandboxClient`, `DockerSandboxClient`, and all seven named
  hosted providers (Blaxel, Cloudflare, Daytona, E2B, Modal, Runloop, Vercel) — all seven
  confirmed present in one search pass, not padded or truncated.
- Handoffs / "agents as tools" / minimal primitive set (agents, handoffs, guardrails,
  sandbox agents) — confirmed.
- Agno: `offload_tool_results`, `AgentFS`, `ResultStore`/`THRESHOLD_CHARS`, `16,000`-char
  default, `result_id` — confirmed verbatim, including the exact threshold number (an easy
  place to misremember as 8K/32K/etc.).
- Agno durable queue: `agno_runs`, `agno_jobs`, `agno_tool_results` table names — confirmed
  exactly as named, not approximated.
- Agno CodeMode: pickle/unpickle semantics and the specific failure example (a `client`
  holding a `_thread.lock` fails to unpickle and is reported "not restored") — confirmed
  down to the specific error class, the detail most likely to be a plausible-sounding
  fabrication if the author hadn't actually seen the source.
- Agno PR references #7207 (three-state per-member offload override) and #8210 (CodeMode
  pickling into AgentFS) — both numbers confirmed as real, matching PRs, not invented IDs.
- Pydantic AI: `output_type`, `Agent(retries={'output': N})`, `AgentRetries`, `RunContext[T]`,
  `ctx.deps`, `deps_type=` — confirmed.
- Pydantic AI nuance ("tool retries cannot be overridden per run — only configured on the
  agent") — confirmed verbatim ("Tool retries cannot be overridden..."); this is the kind of
  asymmetric-budget detail that's easy to get backward, and it's stated correctly.
- `Agent[DepsT, OutputT]` generic typing over dependency and output type — confirmed via
  three independent mirrors including a worked bank-support-agent example showing the exact
  generic parameterization pattern described.

## Adversarial checks that did not break it

- Tried to catch the model_router.py citation as a fabrication (line range didn't match
  current `main`). Resolved by diffing against the PR's own branch snapshot rather than
  current `main` — the citation was accurate at authoring time; the drift is a side effect
  of an unrelated ticket (wq#4355) landing on `main` first. Not a defect in this PR.
- Tried to catch "agent core is still design-only" as stale framing (three code modules
  already exist). The doc itself preempts this ("three of its pieces already exist as code
  and map directly onto the patterns above... additive to that §6, not a rewrite of it") —
  checked that no `agent`/`orchestrator`/`hunter` implementation module exists under
  `limpet/` to contradict it. It doesn't.
- Checked the CodeMode caveat isn't the doc softening an inconvenient finding: the doc
  explicitly declines to adopt CodeMode's live-pickle kernel restore "as-is," citing the
  same unpickle-is-code-execution risk the source itself names — this is the doc correctly
  not recommending something its own source flags as unsafe, not an evasion.

## Non-issues

- The "LIMPET AGENT RUNTIME SPEC" section is framed as additive design guidance ("The spec
  below is additive to that §6, not a rewrite of it") with one explicit non-adoption
  (CodeMode's kernel restore) — consistent with the ticket's own scoping, not overreach.
- Recommendations cite real gaps against real code (sequential critic vs. the Agents-SDK
  parallel-guardrail pattern; missing retry half of an already-partial validation gate) —
  none of the three recommendations invent a capability that doesn't exist to make a point.

## Bottom line

Every falsifiable claim — seven local file:line citations and roughly a dozen external
technical specifics spanning three separate projects — reproduced correctly against
independent re-sourcing. The one line-citation that no longer matches `main` is explained
by a concurrent, later-merged, unrelated ticket and was verified correct against the PR's
own branch snapshot. No escalation.
