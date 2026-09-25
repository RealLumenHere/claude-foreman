# Coral needle-tier dispatch: is any gate model-based, and can TFLite replace it

wq#2623. Written 2026-09-25. Scope/survey only — no code shipped, no gate touched,
no hardware ordered.

**Revised 2026-09-25 (wq#2661, judgment on the wq#2625 verify-pass).** The first
version's §2 claimed to enumerate every model-dispatching surface on the fleet and
missed mailbot entirely — two live per-inbound-email classification gates. The gates
are now enumerated (§2.2), priced (§3), and scored (§5). **The §0 no-go verdict is
unchanged**, and §2.2 gives the reason it could not have been changed by them: mailbot
runs at Cloudflare's edge, so a USB Coral plugged into Lumen is on the wrong side of
the network from it.

Evidence grades match the sibling docs: **A** = direct measurement/read of live code,
**B** = code read with behaviour inferred but not executed here, **C** = inference from
general/vendor knowledge, not fleet-specific and not freshly verified.

---

## 0. Headline

**No-go, fleet-wide, at current traffic.** Every model-based classification/gate surface
found either (a) already has a cheaper non-ML fix in flight, (b) costs too little in
absolute dollars to justify an EdgeTPU training/maintenance pipeline, (c) is
correctness-critical in a way that makes lossy int8 compression the wrong trade, or
(d) does not execute on Lumen at all, so an accelerator plugged into Lumen cannot reach
it without adding a network hop (§2.2, added wq#2661). Zero
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
| mailbot drift screen (`mailbot/src/worker.js:323-362`, live at `:919`) | per inbound email, when the regex hint stays quiet | deepseek-v4-flash, `max_tokens:40` | **binary classifier** — `{"romantic_drift": boolean}` over one ≤2000-char email |
| mailbot reply critic (`mailbot/src/worker.js:246-306`, live at `:1009` and `:1034`) | per drafted reply, up to 2× (once after regen) | deepseek-v4-flash, `max_tokens:300` | **6-boolean multi-label QA gate** over draft + inbound + ≤3 prior outbound |

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

### 2.2 mailbot — missed by the first version, and why

Both mailbot gates were absent from the first version of this doc and are still absent
from `MODEL_ROUTING_AUDIT.md` and `FLEET_COST_PLAN.md` (grade **A**: `grep -n mailbot`
over all three returns zero hits). The miss is not a typo, it is an enumeration-method
gap worth recording, because it will recur:

`MODEL_ROUTING_AUDIT.md`'s §1 surface list was built from `systemctl list-unit-files`,
the timer roster, `crontab -l`, and a source grep for flagship **CLI** flags scoped to
`agents/ services/ agent/ bin/ minerva/` (`MODEL_ROUTING_AUDIT.md:41-42`). Every one of
those four instruments is an *on-Lumen process* instrument. mailbot is a Cloudflare
Worker (`mailbot/wrangler.toml:1-5` — `main = "src/worker.js"`, `workers_dev = false`,
D1 + R2 + `SEND_EMAIL` bindings), deployed to the edge, and it reaches DeepSeek by plain
`fetch("https://api.deepseek.com/chat/completions")` rather than through the LiteLLM
gateway or the `claude` CLI. It runs no systemd unit, no timer, no cron entry, and
contains no CLI model flag, so it is invisible to all four. Widening that grep to
include `mailbot/` would *not* have caught it either — the grep pattern was
`--model opus|fable|…`, which mailbot's source does not contain. `model_routing_audit.py`
does not close the gap either: it computes burn statistics from `_runs/`, the queue DB
and the messenger DB (`model_routing_audit.py:32-37`) and enumerates no surfaces at all,
so the audit's "reproducible" claim covers its numbers, not its surface list. The
recorded fix is in `MODEL_ROUTING_AUDIT.md` §1.1 and wq#2664.

**This is also the architectural disqualifier for the two gates, independent of cost.**
The needle-tier premise is a USB Coral attached to Lumen. mailbot's gates execute inside
a Cloudflare Worker isolate; serving them from a Coral on Lumen means the Worker makes a
network round-trip back to Lumen instead of to `api.deepseek.com`. That *replaces* one
remote call with another — and with a worse one, since Lumen is a single self-hosted box
with no edge-POP presence, and it would become a new hard dependency for a mail path
that currently survives Lumen being down entirely. There is no version of the Coral
pitch that helps a surface on the far side of the network from the accelerator. Any
offload story for these two gates would have to be Workers AI or a quantized model
shipped inside the Worker bundle — a different ticket, different economics, no Coral
in it.

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

**mailbot's two gates, priced here for the first time** (grade **B** on the per-call
arithmetic — prompt sizes and caps read directly from source; grade **C** on volume,
see the caveat below):

| Gate | Input tokens/call | Output | $/call | Calls per inbound |
|---|---|---|---|---|
| `screenRomanticDrift` | ~210 system (`worker.js:316-321`, 841 B) + ≤560 user (subject ≤200 B + body ≤2000 B) ≈ **770 worst case** | ~10 (`max_tokens:40`) | **~$0.00016** | ≤1 — short-circuited by `romanticDriftHint()` on the `||` at `worker.js:919`, so it fires only when the regex stays quiet |
| `runCritic` | ~690 system (`worker.js:232-244`, 2755 B) + ≤1375 user (inbound 2000 B + draft 2000 B + ≤3×500 B prior) ≈ **2065 worst case** | ~120 (`max_tokens:300`) | **~$0.00046** | ≤2 — `worker.js:1009`, then again at `:1034` after a bounded regen |

At deepseek-v4-flash's $0.208/$0.251 per M tokens that is **≤$0.00108 per inbound email**
with every cap saturated and every optional call firing.

Volume: the local D1 mirror (`mailbot/mail.db`, populated by `lumen-mail-sync`) holds 33
inbound across 2026-09-01→09-03 over 8 threads — **~15 inbound/day** — but the mirror's
`sync_state.last_ts` is 2026-09-03 and the unit is `inactive/dead`
(`research/FLEET_SERVICE_ATLAS.md:294`), so this is a stale sample, not current traffic.
Treat it as an order of magnitude, corroborated by the author's own comment on the reply
path: "this inbox is low volume" (`worker.js:204-205`).

That gives **~$0.49/mo at the observed ~15/day**, and **~$3.3/mo even at a deliberately
generous 100/day** — i.e. the entire mailbot classification-gate bill sits *below* the
$0.60/mo residual that §3's minerva paragraph already ruled too small to justify a
training-and-retraining pipeline. Re-pricing with live D1 numbers cannot change that
conclusion without mailbot traffic growing by two orders of magnitude, and if it ever
did, §2.2's edge-vs-Coral problem would still be waiting.

(Out of scope for needle-tier but worth naming while the meter is running: mailbot also
makes two *generative* calls per exchange — `generateReply` (`worker.js:176`,
`max_tokens` 1600-2600) and `generateNotesUpdate` (`worker.js:576`, `max_tokens:300`).
Those dominate mailbot's actual DeepSeek spend and are not classification gates, so they
are not needle-tier candidates under any reading. They are the reason mailbot deserves a
line in `FLEET_COST_PLAN.md` regardless of this doc's verdict — filed as wq#2664.)

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
| mailbot `screenRomanticDrift` binary drift screen | **yes — the best shape on the fleet.** A single bounded-length text in, one boolean out, already the terminal form §6 has to *restructure* the LPP extractor into. Labels are free by distillation (every historical call already produced a boolean) | fails — ~$0.07/mo at observed volume, ~$0.5/mo at 100 inbound/day (§3) | **NO-GO** — and unlike every other row, cost is not even the binding constraint: it runs in a Cloudflare Worker, so a Coral on Lumen is on the wrong side of the network (§2.2). No latency case either — the reply path is asynchronous email, not a live conversational turn |
| mailbot `runCritic` 6-boolean QA gate | partly — fixed label set (6 booleans), but it also emits a free-text `note` and reasons over draft-vs-inbound-vs-history entailment, which is comprehension, not feature matching | fails — ~$0.41/mo observed, ~$2.6/mo at 100/day (§3) | **NO-GO** — shape is marginal, cost is trivial, and it is fail-closed by design (`worker.js:364-367`: a missing or malformed verdict blocks the send). Substituting a lossy int8 classifier into a fail-closed safety gate is the same wrong trade as the `minerva_verify.py` row below, plus §2.2's edge problem |
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

**Why mailbot's drift screen is not a second pilot, despite the better shape** (wq#2661).
`screenRomanticDrift` beats the LPP extractor on every criterion step 1-4 above cares
about: it is already a pure binary classifier over short bounded text, so it needs no
restructuring out of an open-vocabulary extractor first, and its historical verdicts are
free distillation labels in exactly the same way. It loses on the only two things that
made LPP *conditional* rather than flat no:

- **Latency has no claimant.** The LPP pilot's whole payoff is cutting a round-trip off a
  live SAY path that is `await`ed while a human waits (`agent.ts:1367-1371`). mailbot's
  drift screen sits in an asynchronous email reply whose budget is seconds to minutes,
  and which already spends 1600-2600 output tokens on `generateReply` downstream of it.
  Removing ~300 ms there buys nothing anyone can perceive.
- **The accelerator is on the wrong side of the network** (§2.2). LPP runs on Lumen, so a
  Coral on Lumen is a local call. mailbot runs at Cloudflare's edge, so it is not.

So: better shape, no payoff. Recorded here rather than in §6's step list so the next
reader does not re-derive it from the shape alone and conclude it was overlooked.

---

## 7. Recommendation

Do not build a Coral needle-tier dispatch pipeline at current fleet traffic. Every
candidate gate is either not shaped like a fixed-class decision, already has a cheaper
non-ML fix that dominates the arithmetic, or is correctness-critical in a way that makes
accuracy-lossy compression the wrong trade. Revisit if: (a) `article_ingest.py` turns out
on direct read to be a much higher-volume/higher-cost surface than its cadence suggests,
or (b) LPP's SAY-path latency becomes a measured, ticketed complaint, at which point §6 is
the pilot spec to pull off the shelf.

Not a revisit condition: mailbot volume. Its gates would have to grow ~100× to reach even
the dollar threshold this doc already rejects, and §2.2's edge-deployment problem would
survive that growth unchanged — a Coral on Lumen cannot serve a Cloudflare Worker. If
mailbot inference cost ever does become worth attacking, the lever is prompt caching on
`CRITIC_SYSTEM` (2755 B of stable boilerplate per call, the same lever
`FLEET_COST_PLAN.md:218` pulls for `minerva_classifier`), not an accelerator.
