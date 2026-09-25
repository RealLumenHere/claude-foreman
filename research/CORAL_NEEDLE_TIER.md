# Coral needle-tier dispatch: is any gate model-based, and can TFLite replace it

wq#2623. Written 2026-09-25. Scope/survey only — no code shipped, no gate touched,
no hardware ordered.

Evidence grades match the sibling docs: **A** = direct measurement/read of live code,
**B** = code read with behaviour inferred but not executed here, **C** = inference from
general/vendor knowledge, not fleet-specific and not freshly verified.

---

## 0. Headline

**No-go, fleet-wide, at current traffic.** Every model-based classification/gate surface
found either (a) already has a cheaper non-ML fix in flight, (b) costs too little in
absolute dollars to justify an EdgeTPU training/maintenance pipeline, or (c) is
correctness-critical in a way that makes lossy int8 compression the wrong trade. Zero
impl tickets filed. One option — a binary pre-filter ahead of LPP's persona extractor —
is recorded in §6 as a latency-only pilot worth reconsidering if traffic or the
economics change, not acted on now.

This is the same shape of conclusion DIFFICULTY_ROUTING.md reached for gateway routing:
"do not build an embedding classifier or train a router at v1"
(`research/DIFFICULTY_ROUTING.md:47-49`) — our workloads are too low-volume and too cheap
on cloud rungs already for a trained-model investment to pay for itself. Coral changes
the marginal unit cost of inference to ~0, but it does not change the fact that most of
these gates fire too rarely, or at too high a per-decision complexity, for that to matter.

**Method note (read before §1):** the doctrine's mandatory prior-art step
(`fleet-search web`) was attempted first and returned `ok:false` — every general
web-search engine (brave, duckduckgo, google cse, startpage) is suspended fleet-wide, a
known, already-logged outage (wq#2595 missing Brave key; ISSUE-LOG row for wq#2599/2601
confirms the outage is real and unresolved as of 2026-09-25, not a local fluke).
`fleet-search github "coral edgetpu ..."` returned 0 results unauthenticated. This ticket
is mostly internal-codebase archaeology anyway (enumerating this fleet's own dispatch
surfaces is not googleable), but the EdgeTPU-compiler technical constraints in §4 are
grade **C** — vendor-documented behaviour recalled from training, not freshly verified
against current Coral toolchain docs. Re-verify §4 against `coral.ai/docs` before writing
any implementation code, once web search is restored.

---

## 1. Grounding: router.py is confirmed deterministic

The ticket's own grounding note is correct.
`classify_archetype()` (`orchestrator/router.py:769-828`) is a pure keyword/regex rubric
over title+detail text (`_RUBRIC`, `re.search` at `router.py:797`) plus a lane-bias table
and a corroboration gate (wq#778) — no model call anywhere in it. `router.py route`
(the archetype → model/effort chain) is a table lookup over `research/models.yaml`. Same
conclusion the audit already drew for messenger's RE-tier gate: `agent_bridge._RE_TRIGGER_PAT`
is a regex, "heuristic is tight" (`research/MODEL_ROUTING_AUDIT.md:50`). Neither is a
needle-tier candidate — there is no model call to displace.

---

## 2. Every model-dispatching surface on the fleet

`research/MODEL_ROUTING_AUDIT.md:38-64` already enumerated this fleet-wide (wq#750,
grade A/B, reproducible via `model_routing_audit.py`). Reusing that table and marking
shape (classification/gate vs. generative work vs. no model at all), plus two surfaces
that postdate that audit and are added here from direct reads:

| # | Surface | Cadence | Model | Shape | Needle-tier candidate? |
|---|---|---|---|---|---|
| 1 | foreman worker dispatch | per ticket | sonnet/opus/fable | generative (does the work) | no |
| 2 | orchestrator judgment | 6h | fable-5@high | generative (fleet synthesis) | no |
| 3 | verify-pass gate | per shipped diff | sonnet-5@high | generative (reads a diff, judges correctness) | no — needs real code comprehension |
| 4 | messenger chat | per user turn | sonnet | generative (conversation) | no |
| 5 | messenger RE tier | 0.4% of turns | regex, **no model** | n/a | no — already free |
| 6 | `brain_healthcheck.py` | every 5 min | deepseek-v4-flash, `max_tokens:300` | liveness canary (string-equality check on a fixed "HEALTHY" reply), **not a classification decision** | no — F5 already flags it as monitoring the wrong target entirely (`MODEL_ROUTING_AUDIT.md:205-214`); the fix is redirecting the probe, not accelerating a wrong answer faster |
| 7 | `lumen_model_watchdog.py` | ~10 min | 5 cloud + 1 local probe, `max_tokens:5` | liveness probe | no — needs to actually reach the cloud endpoint to prove it's up; a local classifier proves nothing about cloud health |
| 8 | `minerva/model_audit.py` | timer | cloud roster | liveness probe | no, same reason as #7 |
| 9 | `langfuse-ops/evaluator.py` | inactive | mistral-small, LLM-as-judge | classification (quality judge) | no — inactive, nothing to offload; revisit only if reactivated |
| 10 | LPP brain main loop | per game turn | cerebras/deepseek/lumen-vision | generative (plays the game) | no |
| 11 | `research/article_ingest.py` | every 15 min | deepseek-v4-flash | likely classification (feeds intel pipeline) | inconclusive — see §2.1 |
| 12-17 | watchdogs/correctors | various | none | n/a | no |
| 18 | dispatcher lanes | 5 min | none, volume gate only | n/a | no |
| 19 | research cron | cron | none, keyword/HTTP only | n/a | no |

Two more model-based classification surfaces exist that predate/postdate the audit and
are not in its table (found by direct grep, not by the audit script):

| Surface | Cadence | Model | Shape |
|---|---|---|---|
| `minerva_classifier` (`/home/lumen/minerva`, referenced from `foreman/ops/wq2019-proposal-reviewer-fix/minerva_proposal.py:112,121`) | every 4h | deepseek-v4-flash (`research/FLEET_COST_PLAN.md:218`) | two-axis LLM scoring of intel candidates → `classified_intel` table |
| LPP persona extractor (`pokemon/src/persona_llm.ts`, wired at `pokemon/src/live_agent.ts:237`) | fires on every line Lumen SAYs (not every game turn) | deepseek-v4-flash, pinned cheapest rung (`pokemon/src/config.ts:100-114`) | binary self-fact-present gate + open-vocabulary structured extraction |

### 2.1 `article_ingest.py` — inconclusive, flagged not solved

`research/article_ingest.py` is referenced only by path in the audit (`MODEL_ROUTING_AUDIT.md:56`);
the file does not live inside any git repo this worktree's `--add-dir` set can reach (not
in `foreman/research/`, `/home/lumen` itself is deliberately not a git repo — see the
`router.py:70-72` comment on why nothing lands there). I could not read its prompt or
per-call token shape directly, so I am not scoring it go/no-go here rather than guessing
in violation of doctrine. By analogy — same cadence class as `minerva_classifier`, likely
feeding the same intel pipeline — the same economics in §3 probably apply, but "probably"
is not a citation. **Not filing a ticket for this**: the cost ceiling even in the worst
case (a cadence 6x `minerva_classifier`'s, so maybe $15/mo) does not change the verdict in
§3 (the cheap-cloud-rung alternative already dominates the same way). Worth a direct read
if someone with `/home/lumen/research` or wherever it actually lives in `--add-dir` scope
picks this up again.

---

## 3. Why the dollar case fails even where the shape is right

`minerva_classifier` is the cleanest "is this a fixed-class decision" candidate — two
axes, presumably each a small label set, scored per intel item, every 4h. But
`research/FLEET_COST_PLAN.md:218` already prices the *cheaper-cloud* fix for this exact
gate: moving it from deepseek-v4-flash to `glm-5.3-flash-zai` + prompt caching (its system
prompt is stable, 3,485 of 3,637 tokens per call is boilerplate) takes it from **$2.50/mo
to $0.60/mo** — a fix that is a model-alias config change, not a training pipeline. An
EdgeTPU distillation pipeline (label, train, quantize, compile, deploy, retrain on drift)
can only ever save the *remaining* $0.60/mo, and it introduces a standing maintenance
burden (a labeled dataset to keep current, a retraining cadence, an EdgeTPU toolchain, a
USB Coral as a new single point of failure for a 4h-cadence job) that costs far more than
$0.60/mo in engineering attention. Same logic that already killed the embedding-router
idea in DIFFICULTY_ROUTING.md §2.2: "it also puts \[a call\] on the request path... for a
[cheap, already-small workload] is not free" — the machinery costs more than the problem.

LPP's persona extractor has no itemized cost line (too small to appear in
`FLEET_COST_PLAN.md`'s named rows). Back-of-envelope from its own config
(`pokemon/src/config.ts:105-114`): ~150 input tokens (600-char cap), usually a ~10-20
token `{"facts":[]}` reply (the SYSTEM prompt calls this "the common case", `persona_llm.ts:34`),
deepseek-v4-flash at $0.208/$0.251 per M tokens → roughly $0.00003-0.00004/call. Even at a
generous few-thousand SAY-lines/day this is low single-digit dollars/month — smaller than
the minerva case, so the same "machinery costs more than the problem" verdict applies on
cost grounds alone.

---

## 4. EdgeTPU compilability primer (grade C — recall, not fresh-verified; re-check before building)

For a gate to be a real EdgeTPU candidate it needs, on top of the doctrine's own bar
(CNN/MLP only, int8, <8MB):

- **No attention/transformer blocks** — the compiler's supported op set is the
  standard mobile-CNN set (conv2d, depthwise-conv, fully-connected, pooling, concat,
  quantize/dequantize, a handful of elementwise ops). Multi-head attention, softmax over
  a large vocabulary, and layer-norm are either unsupported or fall back to CPU, which
  defeats the purpose.
- **No embedding-lookup (`Gather`) on the TPU itself.** Text input means either (a) a
  fixed-size hashing-trick feature vector (n-gram → hashed bucket → count/TF-IDF, computed
  on the CPU host in plain code, no lookup table, no training needed for the hashing step
  itself) feeding a dense/conv classifier head that *does* run on the TPU, or (b) a small
  pre-baked embedding table read on the CPU host with only the downstream MLP/conv layers
  compiled for the TPU. Either way, "text → embedding → conv/MLP" (the ticket's own
  phrasing) already implies the embedding step is a CPU-side pre-processing step, not
  something the TPU itself accelerates — worth stating explicitly since it changes where
  the latency actually comes from (the network round-trip to a cloud model, not the
  embedding lookup).
- **A single compiled model, not a growing family.** Every additional gate is a separate
  labeled dataset, a separate training run, a separate retrain-on-drift schedule. This is
  the standing cost §3's verdict weighs against $0.60-3/mo problems.

---

## 5. Go/no-go per gate

| Gate | Shape fits EdgeTPU? | $-case | Verdict |
|---|---|---|---|
| `minerva_classifier` two-axis scoring | plausible (fixed label axes) if the two axes are truly small enumerations | fails — cheaper cloud fix already gets to $0.60/mo (§3) | **NO-GO** |
| `article_ingest.py` | unknown (couldn't read the file, §2.1) | by analogy, fails | **NO-GO (by analogy; re-audit if file becomes reachable)** |
| LPP persona extractor — full extraction | no — open-vocabulary structured output, not a fixed class | trivially small (§3) | **NO-GO** |
| LPP persona extractor — binary "any self-fact in this line?" pre-filter only | yes, shape-wise (binary classification over a short line) | dollars trivial either way; the real win is latency removed from the live SAY path (`agent.ts:1367-1371` — this call is `await`ed and deadline-bounded on the critical path every time Lumen speaks), not spend | **CONDITIONAL — recorded as a pilot option (§6), not pursued now** |
| `brain_healthcheck.py` | n/a — it's a liveness canary, not a classifier | n/a | **NO-GO** — wrong problem; F5 already has the real fix (redirect the probe) |
| `lumen_model_watchdog.py` / `minerva/model_audit.py` | n/a — must reach the actual cloud endpoint to prove liveness | n/a | **NO-GO** |
| `minerva_verify.py` hallucination guard (citation check + LLM judge, fail-closed) | no — open-ended factual/citation entailment over arbitrary generated text, correctness-critical | n/a — this is the one place cost is not the deciding factor | **NO-GO** — a lossy int8 classifier substituted for the guard that exists specifically to catch model hallucination is the wrong place to add a second source of model error, regardless of price |
| `langfuse-ops/evaluator.py` | plausible if reactivated | inactive now | **NO-GO (inactive)** |
| verify-pass gate / orchestrator judgment / messenger chat | no — full generative reasoning over variable, often long, context | n/a | **NO-GO** — not needle-tier by shape |
| messenger RE-tier / archetype classifier | already free (regex, no model call) | n/a | **N/A — nothing to displace** |

---

## 6. Pipeline spec, recorded for the one conditional option (not scheduled)

If SAY-path latency on LPP's persona extractor ever becomes worth fixing (it is not
currently ticketed as a complaint — this is a pre-emptive spec, not a response to a
measured problem):

1. **Label**: replay historical SAY lines already logged by the live extractor; the
   binary label is free — `{"facts":[]}` vs. non-empty was already decided by
   deepseek-v4-flash for every historical line, so this is pure distillation, no new
   human labeling.
2. **Feature**: character n-gram (2-4 gram) hashing trick, fixed-size (e.g. 2^12 buckets),
   computed in the same Node process as `persona_llm.ts`, no vocabulary file to ship or
   drift.
3. **Model**: a 2-layer MLP (e.g. hashed-features → 128 → 32 → 1 sigmoid), trained with
   quantization-aware or post-training full-integer quantization (representative dataset
   = a held-out slice of the hashed features) to int8.
4. **Compile**: `edgetpu_compiler` on the resulting `.tflite`; confirm the compiler log
   reports 100% of ops mapped to the Edge TPU (a partial map means the dense layers still
   round-trip through the host CPU per inference, which erases most of the latency win).
5. **Serve**: gate before the network call in `persona_llm.ts`'s `gatewayExtractor()`
   (`persona_llm.ts:92-113`) — run the local classifier first; only call
   deepseek-v4-flash when it predicts "maybe yes" (same escalate-on-uncertainty shape
   DIFFICULTY_ROUTING.md already established for the gateway cascade, §4.3 of that doc).
6. **Cost-save estimate**: dollars saved are the §3 few-dollars/month ceiling minus
   whatever the classifier still escalates — not worth a line item. The actual payoff
   would be cutting a network round-trip (typically hundreds of ms) off the live SAY path
   for the majority "no fact" case. Not pursued now because nothing in this repo currently
   measures that latency as a problem; building the measurement would be the honest first
   step, not the TPU pipeline.

No impl ticket filed for this — it is a documented option for future reconsideration, not
a recommendation to act.

---

## 7. Recommendation

Do not build a Coral needle-tier dispatch pipeline at current fleet traffic. Every
candidate gate is either not shaped like a fixed-class decision, already has a cheaper
non-ML fix that dominates the arithmetic, or is correctness-critical in a way that makes
accuracy-lossy compression the wrong trade. Revisit if: (a) `article_ingest.py` turns out
on direct read to be a much higher-volume/higher-cost surface than its cadence suggests,
or (b) LPP's SAY-path latency becomes a measured, ticketed complaint, at which point §6 is
the pilot spec to pull off the shelf.
