# wq#4364 — Dissection: Observability and Optimization for Limpet (Langfuse, AgentOps, Braintrust, DSPy)

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Sources:** `fleet-search web` 2026-10-19
(SearXNG general engines suspended/CAPTCHA'd fleet-wide, DeepSeek Search fallback used per the
tool's own `note` field — same outage posture as the sibling dissections in this series).
Primary hits: `dspy.ai/3.4.1` (official docs, the GEPA F1 0.41→0.63 example verbatim),
`github.com/ArdurAI/ai-observability-almanac` `tools/agentops.md` and `tools/braintrust.md`
(third-party tool-tracking wiki, dated entries), `github.com/orgs/langfuse/discussions/12110`
and `github.com/plune-ai/cairn` `docs/adr/0004-prompts-in-langfuse.md` (a real ADR showing
Langfuse prompt management wired into a production bot), `spinchange.github.io/vulture-nest`
agent-observability page (OTel `gen_ai` semantic-convention summary). Doc URLs inline per
section. No GitHub API token this session — `fleet-search github` not attempted since the web
engine returned source-repo and ADR hits directly.

Companion to the other dissections in this research lane:
`research/WQ4357_LANGGRAPH_DISSECTION.md` (runtime substrate for the Target Device Wizard),
`design/wq3999_rex_skill_dissection.md` (the recon/5-stance-hunters/reconcile topology these
four tools would instrument), and `limpet/critic.py`/`limpet/obligations.py`/`limpet/benchmark.py`
(the loop, ledger, and eval instrument that already exist in code — this doc maps four
third-party tools onto surfaces that are live, not speculative).

---

## 0. One correction to the ticket's framing

The ticket states AgentOps carries "12pct overhead." No primary AgentOps source (its own docs,
changelog, or the `ai-observability-almanac` entry) gave that figure under this search pass.
The only 12-15% overhead number found is generic and explicitly **not** AgentOps-specific: an
ARMO security-performance blog post framing "12–15% overhead figures from developer
observability" as a category-wide range that "can't transfer to a security buying decision as
published" because it's measured at a Python-decorator layer, not the kernel
(`armosec.io/blog/performance-considerations-for-ai-agent-security`). Treat "~12%" as a
plausible order-of-magnitude for span-decorator-based tracing in general, not a verified
AgentOps-specific benchmark — re-verify against AgentOps's own docs before quoting the number
in anything operator-facing.

## 1. Langfuse — prompts as a versioned artifact, decoupled from code

Source: `github.com/orgs/langfuse/discussions/12110` (a self-hosted operator's own usage
description); `github.com/plune-ai/cairn` `docs/adr/0004-prompts-in-langfuse.md` (ADR, dated
2026-06-08).

- **The core primitive:** `getPrompt(name, {label: "production"})` fetches a named prompt by
  label, not by hardcoded version — the caller never pins a version number in code. A new
  prompt version is created, labeled `candidate`, evaluated, and only then **promoted** by
  moving the `production` label onto it. Rollback is the same operation backwards: move the
  label back. No code deploy is involved in either direction.
- **Config travels with the prompt, not just text.** The discussion-12110 operator stores
  non-text config (which model/provider/endpoint a given agent should use) inside a prompt's
  `config` field and versions it the same way as the prompt body — "I change this often because
  new models drop every week... I need this to be dynamic: no redeployment, no restart, just
  change it and it's live." This generalizes Langfuse from "prompt store" to "any small piece of
  agent config that should be hot-swappable without a release."
  - **wq#4364-specific caveat:** this is the asking operator's own workaround, not a documented
    Langfuse feature — a GitHub Discussion titled "Versioned Config store (separate from
    Prompts) · Discussion #12110" asking Langfuse to ship a first-class config store distinct
    from prompts. Treat "config-in-prompt" as a known pattern people reach for, not an endorsed
    API; a dedicated config store may or may not exist by the time Limpet builds this.
- **Mandatory local fallback (the ADR's load-bearing decision):** `src/prompts/local/*.md|json`
  ships committed prompt text so the bot "works offline and on first start (before seeding into
  Langfuse)" and doesn't crash if Langfuse is unreachable. `PromptRegistry.getPrompt(name, vars?)`
  returns `{text|messages, version, isFallback}` — the caller always knows whether it got the
  managed version or the offline fallback, and every generation links the prompt **version** it
  actually used, so a quality regression is attributable to one specific version, not a vague
  "the prompts changed."
- **Self-hosting:** the discussion-12110 operator runs Langfuse self-hosted already, alongside
  prompt management and observability, and reports no friction specific to self-hosting in that
  thread — consistent with Langfuse's open-source self-host story, though this pass did not
  independently verify infra requirements (Postgres/ClickHouse/Redis footprint) against
  Langfuse's own deployment docs.

## 2. AgentOps — session replay and trajectory-as-graph

Source: `github.com/ArdurAI/ai-observability-almanac` `tools/agentops.md`.

- **Positioning:** "agent session lifecycle monitoring, tool call tracking, and session replay
  for frameworks like CrewAI, AutoGen, and LangGraph" — framework-native integrations, not a
  generic OTel collector bolted on after the fact. Teams use it to "debug multi-agent workflows,
  track which tools agents call and why, and replay sessions to understand failure modes."
- **Metrics are agent-shaped, not request-shaped:** "task completion rates, iteration counts,
  and cost per session" — the unit of measurement is a whole agent session (potentially dozens
  of tool calls and model turns), not one HTTP request/response pair the way conventional APM
  measures.
- **Named gotcha:** "agent-focused, so teams building simpler LLM applications (single call,
  RAG) won't get much more than standard request logging" — the almanac entry itself flags that
  this tool earns its keep specifically on multi-step, multi-tool-call agent loops, which is
  Limpet's shape (REx's recon→stances→reconcile), not a single-call use case.
- **What this catches that conventional monitoring doesn't** (ticket's own framing, consistent
  with the positioning above): a loop that keeps calling the same tool with near-identical
  arguments, or an agent whose state drifts from its own prior turns, is invisible to
  request-level logging (each call looks individually fine) but visible the instant the whole
  session is rendered as one trajectory graph — exactly the failure class a reader-ensemble
  "one leak collapses five opinions into one held five times" risk
  (`design/wq3999_rex_skill_dissection.md:156-157`) would produce if a stance agent silently
  re-ran the same check instead of reading independently.

## 3. Braintrust — eval-first CI/CD, nested trace database

Source: `github.com/ArdurAI/ai-observability-almanac` `tools/braintrust.md`.

- **Category label the almanac gives it is literal:** "Eval-first Observability" — evals are
  not a side feature bolted onto a tracing product, they're the organizing primitive. "$124M
  raised; Notion/Stripe/Vercel; CI/CD quality gates" is the entry's one-line summary.
  Proprietary license (not self-hostable, unlike Langfuse/AgentOps's partial-open posture) —
  a build-vs-buy constraint to weigh, not just a feature comparison.
- **Dated changelog entries in the same source show the product moving toward agent-shaped
  tracing specifically for Claude**: "braintrust@3.29.0... Claude Agent SDK integration cleanup
  for span metadata" (2026-08-27) and "adds inject/extract APIs for distributed tracing across
  service boundaries and avoids capturing message history in AI SDK tool spans" (2026-07-07) —
  i.e., nested spans are the native trace shape, and the product is actively tuning what gets
  captured in agent tool-call spans versus what's elided (message history excluded by default
  in that release), which matters for Limpet given the "mask before print" credential posture
  (Tier-0 instinct 4) — any eval/trace pipeline that captures full message history by default is
  a liability surface, not just a feature.
- **CI/CD gate, concretely:** the almanac's framing ("CI/CD quality gates") plus the category
  label together describe the pattern the ticket means: a scoring function runs against a
  held-out eval set on every PR/deploy, and a regression below a threshold blocks the merge/
  deploy — the same posture `limpet/benchmark.py` (wq#3997) already implements in miniature
  (§7 below), just without CI wiring yet.

## 4. DSPy — typed signatures, GEPA-compiled prompts

Source: `dspy.ai/3.4.1` (official docs home page, fetched live).

- **The core reframe, verbatim from the docs:** "Express your tasks as structured signatures,
  not prompts, to produce maintainable, modular, and optimizable programs." A **signature**
  declares typed inputs/outputs for a task; the actual prompt text sent to the model is
  generated and tuned by DSPy's optimizers, not hand-written and hand-edited by a human.
- **GEPA optimizer, the exact example the ticket cites, confirmed verbatim at the source:**
  ```
  tp = dspy.GEPA(metric=semantic_f1, auto="medium")
  opt = tp.compile(rag, trainset)
  # Before: 0.41 F1
  # After:  0.63 F1
  opt.save("rag.v2.json")
  ```
  A second worked example on the same page, different task (email extraction), shows the same
  shape with concrete cost: "Baseline 62% (gpt-5.4-mini, zero-shot) → Optimized 89%
  (gpt-5.4-mini + GEPA compile) · Cost $2.18 · 200 examples" — i.e., compiling against a metric
  and a labeled trainset is a few-dollar, bounded-cost operation, not an open-ended fine-tune.
- **The mechanism `compile()` is actually doing:** GEPA needs (1) a signature/program to
  optimize, (2) a `metric` — a scoring function taking a prediction and a gold example and
  returning a number, and (3) a `trainset` of labeled examples. It then searches prompt/few-shot
  variations and keeps what the metric prefers, saving the result as a portable artifact
  (`rag.v2.json`) that can be loaded at inference time without re-running the optimizer.
- **Precondition the ticket's own framing states and this confirms is load-bearing:** compiling
  needs a stable action space and a trustworthy metric *before* it's worth running — optimizing
  against a metric that doesn't yet reflect what "good" means for the task just compiles in the
  metric's blind spots. This is why the ticket frames DSPy as a later-stage move ("once RE
  action space is stable, compile prompts instead of hand-tuning"), not a first move.

## 5. Where these four tools actually differ (the decision a stack choice needs)

- **Langfuse** owns the artifact: a prompt (plus optional config) that exists independently of a
  code deploy, versioned, labeled, swappable at runtime. It answers "what prompt/config is
  live, and can I change it without a release."
- **AgentOps** owns the session-level trace/replay view: a trajectory graph over one agent run,
  framework-native (CrewAI/AutoGen/LangGraph), tuned for catching loop/drift failures a
  request-level log can't show. It answers "what did this agent session actually do, step by
  step, and where did it go sideways."
- **Braintrust** owns the gate: eval-first, CI/CD-wired, nested-span tracing, proprietary/
  hosted. It answers "did this change make the agent measurably worse, and should that block
  deploy."
- **DSPy** owns the compile step: typed signatures instead of hand-written prompts, an
  optimizer (GEPA) that tunes against a metric and a trainset, producing a portable compiled
  artifact. It answers "given a metric I trust and a stable task shape, can a search procedure
  beat my hand-tuned prompt."

None of the four substitute for each other — Langfuse without an eval gate just lets you ship
untested prompt changes faster; Braintrust without Langfuse-style prompt versioning has no
artifact to attribute a regression to; DSPy without a trustworthy metric (which Langfuse/
AgentOps/Braintrust together are what make trustworthy) compiles against noise.

## 6. LIMPET VALUE — concrete mapping

**The missing piece today is the eval instrument's CI wiring, not the instrument itself.**
`limpet/benchmark.py` (wq#3997) already is Limpet's "RE success metric": `run_suite()`
(`limpet/benchmark.py:179`) grades the agent against `benchmark.d/*.toml` fixture tasks via
`grade_task()` (`limpet/benchmark.py:127`) and `_aggregate()` computes a single `score` field
— `"score": round(passed_n / total_n, 4) if total_n else 0.0` (`limpet/benchmark.py:236`) — plus
per-benchmark and per-target-class breakdowns (`by_benchmark`/`by_target_class`,
`limpet/benchmark.py:238-239`). This is exactly the scalar Braintrust-style CI/CD gating needs:
**wire `run_suite()`'s `score` into CI, fail the build if it regresses past a threshold**
versus the last-merged baseline (`_done_digests` at `limpet/benchmark.py:170` already supports
incremental/resumed runs against `prior` results, so a CI job can diff against a stored prior
aggregate without re-running everything from scratch). That turns `benchmark.py` from a local
eval script into the Braintrust-shaped deployment gate the ticket asks for, without adopting
Braintrust itself (which is proprietary — `limpet/benchmark.py`'s own docstring already commits
to "Limpet's agent core is still design-only... this module does not invoke a model," so there
is no vendor lock-in risk to swap later if a hosted eval platform becomes worth paying for once
the agent core exists).

**Hunter prompts as versioned artifacts (Langfuse's pattern, not necessarily Langfuse itself).**
The five stance agents REx specifies (`re-bughunt`, `re-safety`, `re-arithmetic`,
`re-lifecycle`, `re-logic` — `design/wq3999_rex_skill_dissection.md:141-147`) are each a prompt
Limpet's analysis loop will need to load, and `limpet/critic.py`'s own module docstring already
names this exact gap: "the critic... a distinct prompt from the one that proposed it"
(`limpet/critic.py:16-18`) is described but **critic.py takes `critic_verdict`/
`critic_rationale` as caller-supplied strings** (`limpet/critic.py:120-121`) — it never loads or
versions the prompt that produced them, because "Limpet's agent core is still design-only...
model routing via LiteLLM is not yet built" (`limpet/critic.py:19-21`, matching
`limpet/model_router.py:14-18`'s identical caveat). When that agent core is built, apply the
`plune-ai/cairn` ADR's pattern directly: each stance prompt gets a `name` + `label` (candidate/
production), a **mandatory local fallback file** so a stance agent still runs if the prompt
store is unreachable (critical for Limpet, which already treats network egress as untrusted by
default — `limpet/model_router.py:6-10`'s China-hosted-API rejection is the same posture), and
every finding records which prompt *version* produced it, so a stance's false-positive spike is
attributable to one prompt change, not "the stances got worse" in the aggregate.

**AgentOps-style trajectory view maps onto the recon→stances→reconcile topology directly,**
once the agent core exists: one session = one Limpet analysis run across all three phases; the
trajectory graph is recon's artifact set feeding five parallel stance branches feeding
reconcile's join — structurally identical to what AgentOps instruments for CrewAI/LangGraph
multi-agent runs. The loop/drift failure this catches that `limpet/critic.py`'s structural gate
cannot: critic.py only validates one finding's evidence citations
(`deterministic_check`, `limpet/critic.py:81-117`) — it has no visibility into whether a stance
agent, across an entire session, kept re-deriving the same conclusion via different wording (the
"one leak collapses five opinions into one held five times" risk,
`design/wq3999_rex_skill_dissection.md:156-157`). A session-level trajectory view is the
instrument for catching that pattern; a per-finding structural gate structurally cannot.

**DSPy compilation is correctly sequenced last, after §6's gate exists.** Once `benchmark.py`'s
`score` (§6) is CI-gated and stable, and hunter prompts are versioned artifacts (Langfuse
pattern) with session-level visibility into failures (AgentOps pattern), *then* the five stance
prompts are strong GEPA-compilation candidates: each stance already has a metric
(`grade_task`'s pass/fail against `benchmark.d/*.toml` fixtures) and a trainset (the fixture
tasks) that compilation needs by precondition (§4). Compiling before the action space is stable
— i.e., before REx's recon/stances/reconcile shape and the finding schema
(`design/wq3999_rex_skill_dissection.md:184-201`, the source/sink/guard/principal four-tuple) are
settled — would optimize prompts against a metric that doesn't yet reflect the real task, the
exact trap §4 names.
