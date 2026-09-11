# Difficulty-based model routing at the LiteLLM gateway

wq#1711, proposal `9f3e9544212a`. Written 2026-09-11. Design and survey only —
nothing in here has been applied, and no live gateway file was touched.

Evidence grades match the sibling docs: **A** = direct measurement, **B** =
code/config read with the behaviour confirmed by executing it, **C** = inference.

---

## 0. Headline

Three findings decide the design, and two of them cut against the ticket's framing.

1. **The DeepSeek spend the ticket targets has already mostly been retired.** LPP
   — 81% of fleet spend — moved off `deepseek-v4-flash` onto `glm-5.3-flash-zai`
   on `lpp-glm.env` and is measured at $54/mo against a $137.64/mo baseline
   (`FLEET_COST_PLAN.md` §8.3, 275 live turns, grade **A**). What remains on
   DeepSeek is roughly $25/mo: the `ops-emergency` recurring job on the
   *reasoning* rung ($12.42/mo), lumen-agent's output-heavy leg ($6.57), the
   ops-emergency light leg ($3.36), the minerva classifier ($2.50), and LPP's own
   persona extractor (`LPP_BRAIN_PERSONA=deepseek-v4-flash`, fires after every
   line Lumen speaks). Difficulty routing is not the lever that cuts the DeepSeek
   bill, because that bill is already small. It is the lever that keeps the next
   workload from rebuilding one.

2. **Between cloud rungs, difficulty routing is barely worth the machinery; against
   the free local rung it is worth everything.** The break-even escalation rate
   for a post-hoc cascade is `e* = 1 - c_lo/c_hi`, independent of how much traffic
   you route (derivation in §3). Across our four real workload shapes that is
   **55–73% between paid rungs** and **100% against `qwen36-local`**. A cascade
   whose cheap rung is free cannot lose money — only latency and capacity. So the
   binding constraint on this fleet is not router accuracy. It is whether Isis can
   take the load (wq#1623 saturation) and whether the request is text-only
   (wq#1589 / D3).

3. **The signal worth building is post-hoc, not pre-hoc.** Our traffic is
   dominated by one caller with one prompt shape, so a pre-call classifier has
   almost no across-request variance to find — 4,635 input tokens every turn,
   whether the turn is "walk north" or "beat Brock". What does separate easy from
   hard is what happened *after* the call, and LPP already computes it: 12
   consecutive non-progress turns (`pokemon/src/brain.ts:74`), fired **1,092
   times between 2026-08-23 and 2026-09-11**, ~57/day (grade **A**,
   `fallback_stats.jsonl`). The design below makes that signal a fleet contract
   instead of one program's private variable.

Recommendation: **build the gateway hook in shadow mode, standardise the escalate
header, and spend the first live rung on the free local one.** Do not build an
embedding classifier or train a router at v1.

---

## 1. What the gateway actually looks like

Probed 2026-09-11 (grade **A**, `curl -s http://localhost:4000/health/readiness`):

```
litellm_version 1.83.10, db connected, log_level WARNING
```

The proxy config lives at `/home/lumen/agent/litellm_config.yaml`, outside git
and outside a BUILD worker's sandbox — the established convention is to ship a
hand-merge fragment (wq#897/948/1299/1406/1623), which §9 does.

Rungs and measured rates, from `FLEET_COST_PLAN.md` §3 (least-squares fits over
the real spend rows, not list prices; grade **A** except where marked):

| rung | input $/M | output $/M | notes |
|---|---|---|---|
| `qwen36-local` (Isis) | 0 | 0 | text-only; single decode slot; saturates (wq#1623) |
| `glm-5.3-flash-zai` | 0.15 list | 0.50 list | reasoning is mandatory; cache hits measured at 0% (wq#1616) |
| `deepseek-v4-flash` | 0.208 | 0.251 | 59.2% transport failure rate in the 35-day window (D4) |
| `cerebras-oss-120b` | 0.200 | 0.793 | no prompt cache at all |
| `deepseek-v4-pro` | 0.432 | 1.232 | the reasoning rung |

Two existing mechanisms are adjacent to this work and must not be confused with it:

- `router_settings.fallbacks` fires on **failure**, not on difficulty, and the
  `qwen36-local ↔ cerebras-oss-120b` pair is already a documented cost leak
  (D2 / wq#1588). Difficulty routing must stay a separate mechanism or the leak
  becomes harder to see.
- `foreman/router.py` + `models.yaml` route *Claude worker dispatch* over OAuth,
  not the gateway. Its own difficulty problem is real — 34.8% of tickets
  over-rank into `research-synthesis` on the bare word "research"
  (`MODEL_ROUTING_AUDIT.md` F1, wq#1755) — but it is a different surface with a
  different cost currency and is out of scope here.

---

## 2. Survey

### 2.1 Learned binary routers — RouteLLM

Ong et al., *RouteLLM: Learning to Route LLMs with Preference Data* (LMSYS /
Berkeley, 2024, arXiv:2406.18665). Trains a router on ~80k Chatbot Arena
preference pairs to predict, for a query, the probability that the strong model
beats the weak one; a threshold α on that probability sets the strong-call
fraction. Four routers: similarity-weighted Bradley-Terry ranking, matrix
factorisation, a fine-tuned BERT classifier, and a causal-LLM classifier.
Authors report >85% cost reduction on MT-Bench while retaining 95% of GPT-4
quality, ~45% on MMLU, and — the part that matters for us — that routers trained
on one model pair transfer to a different pair without retraining, because they
learn query difficulty rather than model identity.

**What transfers:** the calibration discipline. The threshold is meaningless in
the abstract and must be fitted on your own traffic to hit a target escalation
rate. §3 gives our version of that number.

**What does not:** the training data. RouteLLM's signal comes from human
preference votes over open-domain chat. We have no preference data, and our
dominant workload is not chat — it is one program emitting a fixed-shape prompt
59,421 times a month. The learned router has nothing to separate.

*Related:* Ding et al., *Hybrid LLM: Cost-Efficient and Quality-Aware Query
Routing* (Microsoft, ICLR 2024) trains a DeBERTa router on quality-gap labels and
reports ~40% fewer large-model calls at no quality drop. Hu et al.,
*RouterBench* (2024) is the standard reminder that routers must be scored on the
cost-quality Pareto frontier, never on accuracy alone.

### 2.2 Embedding decision layers — semantic-router

Aurelio Labs' `semantic-router`: each route carries example utterances, an
encoder embeds them into an index, and an incoming query is assigned by cosine
similarity above a fitted threshold. Millisecond-scale, deterministic, auditable,
no training run — you write exemplars.

**What transfers:** the shape is a good fit for *intent* gating, and its
threshold-fitting (`fit()` over labelled utterances) is the right way to set any
similarity cutoff we ever adopt.

**What does not:** it routes by topic, not by difficulty. Difficulty only appears
insofar as your exemplars smuggle it in. It also puts an embedding call on the
request path, which for a 4,635-token prompt is not free. **Rejected for v1**;
revisit only if §6's shadow measurement shows within-caller difficulty variance
that the cheap features miss.

### 2.3 Cascades and confidence — the branch we are actually taking

- **FrugalGPT** (Chen, Zaharia, Zou, 2023, arXiv:2305.05176). Call models in
  ascending cost; after each, a small trained scorer (DistilBERT over the query
  and the generation) emits a reliability score, and a learned per-stage
  threshold decides accept-or-escalate. Authors report matching GPT-4 quality on
  several benchmarks at up to 98% lower cost. This is the canonical cascade.
- **AutoMix** (Madaan et al., NeurIPS 2024). Uses few-shot *self-verification* by
  the small model as a noisy accept signal, then a POMDP meta-verifier to decide
  the escalation across three rungs — built specifically for the case where you
  have no trained scorer and only black-box API access. That is our case.
- **Chow's rule** (1970) is the theoretical floor: given calibrated posteriors,
  thresholding confidence is the optimal reject rule. Every cascade above is a
  practical approximation of it, and every one of them fails in the same place —
  calibration.
- **Token-level uncertainty** (Gupta et al., *Language Model Cascades: Token-Level
  Uncertainty and Beyond*, ICLR 2024): mean sequence log-probability is a weak
  deferral signal; quantile aggregations over token logprobs do better, and a
  small trained rule over intermediate embeddings does better still.
- **Self-consistency as confidence** (Wang et al., 2022): sample k answers, defer
  when they disagree. Costs k× the cheap rung — which our §3 arithmetic says is
  affordable here, since one `deepseek-v4-pro` call costs 2.4–3.7× one cheap call.

**Verbalized confidence** ("rate your certainty 1–10") is the one method in this
family we should refuse. It is poorly calibrated and, worse, it is *model
self-report*: a rung that is wrong is usually also wrong about being wrong. Every
signal we adopt must be grounded in an outcome outside the model.

### 2.4 Production routers

OpenRouter's `auto`, Martian, NotDiamond, Unify, and Portkey's conditional
routing all sell the RouteLLM shape as a service. They are worth knowing about and
worth not buying: each adds a hop, a vendor, and a place for a credential to
live, to solve a problem that §3 shows is worth $25/mo on this fleet.

---

## 3. The arithmetic that decides the design

A post-hoc cascade pays the cheap rung on every routed request and the expensive
rung again on the escalated fraction `e`. For routed fraction `f`, against always
calling the expensive rung:

```
E[cost] = (1-f)·c_hi + f·[(1-e)·c_lo + e·(c_lo + c_hi)]
saving  = f·[ (c_hi - c_lo) - e·c_hi ]
```

Saving is positive iff **`e < e* = 1 - c_lo/c_hi`**. The routed fraction `f`
cancels: routing more traffic scales the win but does not change whether there is
one. Computed over our measured rates and four real workload shapes
(`ops/wq1711-difficulty-router/breakeven.py`, grade **A** for the rates,
**B** for the shapes):

| shape (avg in / out) | cheap → expensive | $/req cheap | $/req exp | e* |
|---|---|---|---|---|
| LPP turn (4,635 / 432) | `qwen36-local` → `glm-5.3-flash-zai` | 0 | 0.000911 | **100%** |
| LPP turn | `glm-5.3-flash-zai` → `deepseek-v4-pro` | 0.000911 | 0.002535 | 64% |
| ops-emergency (2,236 / 1,162) | `deepseek-v4-flash` → `deepseek-v4-pro` | 0.000757 | 0.002398 | 68% |
| minerva classifier (3,485 / 152) | `glm-5.3-flash-zai` → `deepseek-v4-pro` | 0.000599 | 0.001693 | 65% |
| lumen-agent (1,743 / 1,984) | `deepseek-v4-flash` → `deepseek-v4-pro` | 0.000861 | 0.003197 | 73% |

Three consequences, and they are the whole design:

1. **A cascade against the free rung cannot lose money.** `c_lo = 0` gives
   `e* = 100%`: even if every single local answer is rejected and re-run in the
   cloud, spend is unchanged. The only costs are latency and Isis's decode slot.
   This is the single highest-value routing move available, and it needs no
   classifier at all — just "try local first unless a hard gate says no".
2. **Between paid rungs the tolerance is enormous.** A router that escalates half
   the time still saves money on every pair in the table. This kills the argument
   for a trained router: we do not need precision, we need a signal that is right
   more than ~40% of the time.
3. **Cheapest is workload-dependent, so the ladder must be per-alias.** On the
   output-heavy lumen-agent shape, `glm-5.3-flash-zai` ($0.001253/req) is *more
   expensive* than `deepseek-v4-flash` ($0.000861/req) — glm's output rate is 2×
   DeepSeek's. A single fleet-wide "cheapest rung" constant would route that
   workload the wrong way.

---

## 4. Design

### 4.1 Split the decision by what each layer can see

| layer | sees | owns |
|---|---|---|
| caller | the task, its own outcome history | **when to escalate** |
| gateway pre-call hook | one request body | **which rung serves it** |
| `router_settings.fallbacks` | transport errors | **failure retry only** |

The gateway cannot judge whether an answer was good, and the caller should not
have to know rung prices or which rung is saturated this minute. Splitting it
this way means a caller opts in by asking for one alias and, optionally, setting
one header — it never names a model.

### 4.2 The pre-call hook

LiteLLM 1.83.10 loads a `CustomLogger` subclass via
`general_settings.callbacks`; its `async_pre_call_hook(user_api_key_dict, cache,
data, call_type)` receives the parsed request and may return a modified `data`,
including a different `data["model"]` (grade **B** — documented API for the
installed version; the shadow rollout in §6 is what converts this to **A**).

Implementation: `ops/wq1711-difficulty-router/difficulty_router.py`. The decision
core is a pure function, so it is tested without LiteLLM, a network, or a running
proxy — 16 tests, all passing (§8).

Decision order, cheapest-first with hard gates:

1. **Not an auto-alias → passthrough.** Pinned callers are untouched. Installing
   the hook changes nothing until someone asks for `lumen-auto`.
2. **Escalate signal present → top rung**, reason recorded.
3. **Capability gates**, in this order, each removing rungs:
   - image parts in the request → drop non-multimodal rungs. This is a *hard*
     gate: `qwen36-local` answers `Content type 'image_url' is not supported by
     selected model` (D3 / wq#1589), and routing an image turn there is how the
     circular-fallback leak started.
   - caller asked for `reasoning_effort: medium|high` or non-disabled `thinking`
     → drop non-reasoning rungs. `low` is explicitly **not** a difficulty signal:
     LPP pins `reasoning_effort: low` on its cheapest leg (`config.ts:45`), and
     reading that as "hard" would promote the entire main loop.
   - estimated prompt tokens over the rung's window, or requested `max_tokens`
     over its output ceiling → drop.
   - rung's breaker is open → drop.
4. **Otherwise take the cheapest survivor.** If nothing survives, use
   `escalate_to` — failing open onto a rung that works, never onto no rung.

Prompt size is estimated as chars/4 rather than with a real tokeniser. It only
ever gates a coarse ceiling, and a tokeniser call on the request path for a 4.6k
prompt is a real latency cost for no decision benefit.

### 4.3 The escalate signal — the fleet contract

**Request header `x-lumen-escalate: <reason>`**, or `metadata.lumen_escalate` in
the body for clients that cannot set headers. Present means "start at the top
rung". The reason string is free text and is written into request metadata, so it
lands in `LiteLLM_SpendLogs` and escalation rate per caller becomes measurable.
Without that accounting the router is unfalsifiable, which is the failure mode of
every routing layer that gets installed and never revisited.

Rules:

- **The signal must be outcome-grounded.** Allowed: no progress for N turns, a
  parse failure on the previous answer, a failed verification, an operator
  reopening a ticket. Not allowed: the model's own opinion of difficulty (§2.3),
  and not allowed: a transport error — that is `fallbacks`' job and mixing them
  makes D2-class leaks invisible.
- **One escalation per request.** No recursion, no ladder-climbing. The
  expensive rung is the terminal state.
- **Budget beats escalation.** If honouring the signal would breach the key's
  budget, serve the cheap answer already in hand and log the refusal. A cost
  control that a difficulty signal can override is not a cost control.

Signals that already exist and should emit this header rather than pick models
themselves:

| caller | signal | evidence |
|---|---|---|
| LPP brain | 12 consecutive non-progress turns | `brain.ts:71-87`; 1,092 fires over 19 days (**A**) |
| LPP actRetry | previous turn produced no parsable ACT | `config.ts:62`; already a different-model cascade |
| messenger | RE-tier heuristic, 0.4% of turns | `MODEL_ROUTING_AUDIT.md` §1 surface 5 (**A**) |
| foreman | `attempt` / `reopened_count` on a ticket | `wq` schema |

LPP's brain is the migration template: it already computes the signal and already
holds a per-rung breaker with the same 2-fail/600s shape the hook uses
(`brain.ts:119`). The change is that it stops naming `LPP_BRAIN_ESCALATE` and
starts sending the header — which moves rung choice next to the price table.

### 4.4 Latency, which is the real budget here

Cascading doubles latency on the escalated fraction. LPP's own budget is 120s for
the profile rung and 30s per fallback rung (`config.ts:190,193`), and Isis holds a
single decode slot that an aborted request does not free (wq#898). So the
local-first cascade must keep the local attempt on a *short* deadline — the
fallback-tier 30s, not the primary 120s — and rely on the breaker to stop
offering a rung that just missed one. This is why the hook mirrors the LPP
breaker rather than inventing a new policy.

---

## 5. Deliberately not built at v1

- **No embedding classifier, no trained router.** §3 says we need a signal that
  is right ~40% of the time; §0 says our dominant caller has almost no
  across-request variance to classify. Both point the same way.
- **No self-consistency sampling.** Affordable (k=2 on a cheap rung still beats
  one pro call) but it triples the local rung's load, and capacity is the binding
  constraint.
- **No logprob-based confidence.** Worth having, but none of our rungs is
  confirmed to return `logprobs` through the gateway. §8 gives the probe; until
  someone runs it this is speculation.
- **No change to `router_settings.fallbacks`.** Separate mechanism, separate
  ticket (wq#1588).

---

## 6. Rollout

**Phase 0 — shadow, 7 days.** Merge §9's fragment with `shadow: true`. The hook
computes and logs its decision; `data["model"]` is never rewritten. Point one
low-stakes caller at the `lumen-auto` alias (alias resolves to that caller's
current rung, so this is a no-op by construction).
*Acceptance:* `lumen_router_model` appears in `LiteLLM_SpendLogs` metadata on
≥95% of that caller's requests, and the counterfactual — sum of decided-rung
prices against actual spend over the same rows — is computable. If the shadow
decisions are wrong, they are wrong for free.

**Phase 1 — one caller live.** Flip `shadow: false` for the minerva classifier
or the ops-emergency recurring job. Both are small ($2.50 and $12.42/mo), both
have stable prompt shapes, and neither is on a human-facing path. Keep a
per-alias kill switch.
*Acceptance:* spend on that key falls, and the quality canary (§7) shows no
regression over a full week.

**Phase 2 — LPP local-first.** Add `qwen36-local` as the cheapest rung for LPP's
text-only turns, with the image gate and the short local deadline from §4.4.
This is where the money is: `e* = 100%` means the only way to lose is latency.
*Prerequisite:* wq#1623's saturation guard is landed and Isis's queue depth is
observable. Do not start Phase 2 before that.

**Not in this plan:** switching the escalate rung. `deepseek-v4-pro` stays the
top of the ladder; this design changes what happens *below* it.

---

## 7. Detecting the quality regression

The honest difficulty with any cheap-rung move is that damage is invisible —
spend drops immediately and quality drops silently. Two canaries already exist
and neither needs new infrastructure:

1. **LPP's own progress metric.** Milestones and badge rate per 1,000 turns is
   the fleet's only genuinely grounded quality signal — an objective outcome, not
   a judge's opinion. Compare the week before and after Phase 2.
2. **`langfuse-ops/evaluator.py`**, the LLM-as-judge at 5% sample, currently
   inactive (`MODEL_ROUTING_AUDIT.md` §1 surface 9). Re-enable it against the
   Phase 1 caller only. A judge is weak evidence in the absolute, but it is
   adequate for a *paired* before/after on the same prompts.

Stop rule: if either canary moves against us by more than its own week-to-week
noise, revert the alias to `shadow: true`. Reverting is one line and needs no
caller change — that is the reason the alias indirection exists.

---

## 8. Open questions, with the probe that answers each

| # | question | probe | why it matters |
|---|---|---|---|
| 1 | Does `async_pre_call_hook`'s returned `data["model"]` actually re-target the deployment in 1.83.10, or is the model resolved earlier? | Phase 0 shadow + one deliberate live rewrite on a throwaway alias; compare `model` in the SpendLogs row against the requested alias | the design's load-bearing assumption (grade **B** today) |
| 2 | Do any of our rungs return `logprobs` through the gateway? | `curl -s $GW/v1/chat/completions -H "Authorization: Bearer $KEY" -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"hi"}],"logprobs":true,"top_logprobs":1}'` — key from the 0600 file, never echoed | decides whether §2.3's token-level confidence is available at all |
| 3 | What is Isis's real concurrent capacity for LPP-shaped turns? | replay 200 recorded LPP prompts at the live turn rate against `qwen36-local` with a 30s deadline; record p50/p95 and slot-miss rate | Phase 2 is capacity-bound, not accuracy-bound |
| 4 | Is `LPP_BRAIN_FALLBACK_MODELS` still defaulting to the retired `mistral-small`? | read the live `lpp-glm.env`; the repo default at `config.ts:198` lists it and D7 says the rung is gone from the config | a fallback pointing at a nonexistent rung fails closed and nobody notices |
| 5 | What fraction of LPP turns are text-only? | count image parts over a day of recorded turns | sets the ceiling on Phase 2's saving; if the vision valve fires rarely this is most of them |

Question 4 is a live-defect candidate, not just an open question.

---

## 9. Artifacts

| path | what |
|---|---|
| `ops/wq1711-difficulty-router/difficulty_router.py` | decision core + LiteLLM hook adapter |
| `ops/wq1711-difficulty-router/test_difficulty_router.py` | 16 tests, `python3 -m pytest … -q` → `16 passed` |
| `ops/wq1711-difficulty-router/breakeven.py` | reproduces §3's table |
| `ops/wq1711-difficulty-router/litellm_config_snippet.yaml` | hand-merge fragment, shadow mode, plus the `CustomLogger` shim |

## 10. Follow-up tickets this doc implies

1. Install the hook in shadow mode and run the 7-day counterfactual (§6 Phase 0).
2. Emit `x-lumen-escalate` from LPP's brain instead of switching models locally,
   keeping the existing 12-turn signal (§4.3).
3. Answer probe 2 (logprob availability) and probe 3 (Isis capacity) — both are
   blockers for later phases, neither blocks Phase 0.
4. Check the live `lpp-glm.env` fallback list against the retired `mistral-small`
   rung (probe 4).
5. Give the ops-emergency recurring job its own key and rung — it is 64
   calls/day on the *reasoning* rung for what `FLEET_COST_PLAN.md` §4 calls a
   timer, and it is the largest remaining DeepSeek line item.
