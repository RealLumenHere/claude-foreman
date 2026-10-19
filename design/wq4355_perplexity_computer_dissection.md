# wq#4355 — LIMPET DISSECT: Perplexity Computer, task-aware multi-model routing

Status: research/design. Author: build worker, 2026-10-19. Dissects Perplexity
Computer (launched 2026-02-25) across the five axes the ticket names, then turns
the adoptable patterns into a concrete upgrade of Limpet's routing layer
(`limpet/model_router.py`). Companion to the agent-product reads
(`design/wq4336_grok_bot_dissection.md`, `design/wq4338_coding_agents_dissection.md`,
`research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md`).

## 0. Identity, license, grounding

- Product **Perplexity Computer** by Perplexity AI. Launched **2026-02-25** as a
  "universal digital worker". **Closed/proprietary SaaS** — not adoptable as code;
  this is a *pattern* dissection, not a vendor/wrap candidate.
- **Grounding (live, not memory):** this product postdates useful model memory, so
  every quoted claim below was fetched 2026-10-19 via `fleet-search web` (SearXNG
  general engines were suspended/CAPTCHA'd; the keyed DeepSeek-Search fallback
  served the results — wq#999/wq#4145 outage, noted so the claims are re-checkable):
  - Perplexity launch coverage (gigazine, 2026-02-26): 19-model routing, subtask
    decomposition, connectors, parallel execution.
  - SPACE announcement coverage (2026-07-16): "Sandboxed Platform for Agentic
    Computer Execution" — the Firecracker-microVM sandbox behind Computer.

Perplexity's own framing: *"a universal digital worker that routes work to 19 AI
models"* — it breaks a task into subtasks, spawns a subagent per subtask, and each
subagent runs inference/search/coding/API calls, aggregating results for the user.

---

## 1. The 19-model backend, role-specialized

**Verified role assignment** (the ticket's framing was right; the exact model names
and one extra role are pinned here from the live fetch):

| Role / need              | Model Computer routes to          | Ticket said |
|--------------------------|-----------------------------------|-------------|
| Core reasoning / heavy lifting | **Claude Opus 4.6** (core inference engine) | "Claude Opus for reasoning" ✓ (4.6) |
| Deep research + subagent creation | **Gemini**                   | "Gemini for deep research" ✓ |
| Lightweight / speed tasks | **Grok**                         | "Grok for speed" ✓ |
| Long-context recall + broad web search | **GPT-5.2 (ChatGPT 5.2)** | "GPT-5.2 for long-context" ✓ |
| Image generation          | **Nano Banana**                  | (not in ticket) |
| Video                     | **Veo 3.1**                      | (not in ticket) |

Up to **19** models total; the six above are the named specialists. The key pattern:
**the model is chosen by what the subtask needs, not named by the user.** Opus is the
orchestrator/reasoning core; the others are need-specialists it delegates to.

**Limpet read:** Limpet is a 32GB/iGPU box (`design/wq3806_limpet_architecture.md:260-266`),
so it cannot host 19 frontier models — the adoptable pattern is not the *fleet size* but
the *mechanism*: a need → specialist map, with a resident model as the always-available
core (Limpet's `resident_model` plays Opus-core's "always there" role; opt-in specialists
play the delegated roles). See §6.

## 2. Automatic subtask→model routing (the classifier)

Computer *ideates* subtasks from one user goal and assigns each to the best-fit model
with **no user model selection**: "Everything from inference to execution is done
automatically." The orchestrator classifies each subtask by requirement (research vs.
fast-answer vs. long-context vs. image) and routes accordingly.

**Limpet gap this exposes.** Today Limpet routes by a *declared capability* a plugin
manifest hard-codes per step (`limpet/manifest.py:57`
`MODEL_CAPABILITIES = ("general","re-naming","re-types","vision")`;
`limpet/model_router.py` resolves it). That is operator-static: the plugin author
decides the capability at authoring time, and an operator maps capability→model in
config. There was **no layer that classifies a subtask by its *requirement profile*
at runtime** — the Computer pattern. This ticket adds that layer (§6).

## 3. User override of the orchestrator (the escape hatch)

Verified: users can **"manually assign specific models to specific subtasks if they
prefer more control"** and **"apply spending controls per subtask"**. The automatic
router is the default; the manual pin is the escape hatch.

**This is the escape hatch our routing needs.** A classifier that is always right
does not exist; an RE operator who knows a given firmware blob needs the big model
must be able to say so and have it stick over the classifier. Implemented in §6 as an
`override` that wins outright over the policy — and, per Perplexity, bypasses
classification entirely (the user is naming a *model*, not a *need*).

## 4. Isolated compute per task (SPACE)

Computer runs each task in **SPACE** — "Sandboxed Platform for Agentic Computer
Execution". Verified properties:

- **Per-task disposable Firecracker microVM**, destroyed when the task ends; ~60ms
  spin-up; production-proven at "tens of millions of reconnects".
- **Pause / resume / branched execution** — a long task can be snapshotted and
  resumed; branches explore alternatives from a snapshot.
- **User-brought encryption keys**; one task's sandbox cannot read another's data.
  Credential handling is explicitly the hard boundary (snapshots taken after an agent
  touches credentials are the tricky case).

**Limpet read.** Limpet already has workspace isolation per target
(`docs/settings-spec.md` §5 workspace isolation; `limpet/workspace_*`), which is the
analogue of SPACE's per-task sandbox. The *adoptable delta* is **snapshot/resume of a
long RE session** and the credential-boundary discipline — Limpet's instinct-4
mask-before-print and 0600-key posture already matches SPACE's "don't snapshot a
credential into a reusable image". No code change here; flagged for a workspace ticket.

## 5. Long-horizon background execution

Verified: Computer runs tasks "end to end" in the background "for hours" (SPACE is
explicitly built for "persistent, long-running sessions … hours or days", vs. legacy
sandboxes built for short-lived code execution), and the user "run[s] other tasks in
parallel". The ticket's "hours/months" is the marketing ceiling; the *documented*
figure is hours-to-days.

**Limpet read.** The fleet already runs long background work via the BUILD-worker /
`lumen_busy task_start` / detached-systemd pattern (CLAUDE.md busy-key protocol). The
adoptable idea is **per-subtask backgrounding with a resume handle**, which maps to the
pause/resume in §4 rather than to routing. Out of scope for this ticket's code change.

---

## 6. LIMPET VALUE — the routing upgrade (landed this ticket)

The ticket: *"Replace static LiteLLM routing with a task-classifier → model-policy
layer. For each RE subtask classify the requirement profile (vision-heavy,
long-context, speed-critical, code-gen) and route to a model policy. Model routing is
an orchestration concern, not a config file."*

Landed in `limpet/model_router.py` (kept pure/offline — the caller still places the
LiteLLM call, same posture as the existing `resolve_model`, so no non-existent client
is speculatively wired):

1. **Requirement-profile taxonomy** — `REQUIREMENT_PROFILES = ("vision-heavy",
   "long-context", "speed-critical", "code-gen")` (`limpet/model_router.py:40`), the
   ticket's exact four. A profile says what a subtask *needs*; it is the Computer-style
   classification axis, orthogonal to the authoring-time `MODEL_CAPABILITIES`.

2. **Policy resolution** — `resolve_profile(profile, *, profile_models,
   resident_model, override=None)` (`limpet/model_router.py:66`). `profile_models` is
   the operator's **policy** (profile → specialized fleet model); an unconfigured
   profile falls back to the resident model (opt-in, 32GB ceiling). An unknown profile
   **fails closed** (`RoutingError`) — a classifier emitting a bad profile is a caller
   bug, not a silent default (wq#2630 fail-closed).

3. **User-override escape hatch** — the `override` argument (Perplexity §3) wins over
   the classifier/policy **outright** and is *not* validated against the profile set,
   because the user is naming a model, not a need. Added to both `resolve_profile`
   and `resolve_step` (`limpet/model_router.py:88`) so an operator can pin either a
   requirement-profile route or a manifest step to a chosen model.

Why this is the right altitude: routing by *runtime requirement* (not authoring-time
capability or a static config entry) is exactly Perplexity's "orchestration, not a
config file" point. The profile layer sits *above* capabilities — a future agent core
classifies a subtask into a profile, this module resolves the profile to a model via
the operator's policy, and the override lets a human overrule it. Still a pure
decision: no model client, offline-testable.

**Not adopted (deliberately):** the 19-model fleet (box can't host it); SPACE's
microVM-per-task (Limpet already has workspace isolation; snapshot/resume is a separate
workspace ticket); background hours/months execution (already covered by the
busy-key/detached-systemd pattern). Those are noted in §§1,4,5 so a later ticket can
pick them up without re-deriving.

## 7. Verification

- `limpet/model_router.py:40,66,88` — the three landed pieces.
- `cd /home/lumen/worktrees/wq4355-a1/limpet && python3 -m pytest test_model_router.py -q`
  → 14 passed (7 pre-existing + 7 new for the profile layer and override, including a
  falsifiable bypass probe `test_user_override_bypasses_profile_validation`).
