# Activation probes on the local open-weight rung — feasibility

wq#1716, proposal `4c6e5ba328a9`. Written 2026-09-11 by `fworker-wq1716`.
Research and live measurement only — no service was reconfigured, no model was
pulled, nothing was routed differently. Every probe against Isis was a bounded
read-only HTTP call (bridge-safe per `FLEET_MAP.md`).

Evidence grades match the sibling docs: **A** = direct measurement taken for this
doc, **B** = code/config read with the behaviour confirmed by executing it,
**C** = inference or recollection, not verified here.

---

## 0. Headline

**Verdict: research-grade only, and not the research worth doing first.**

Three findings decide it, and two of them cut against the ticket's framing.

1. **The serving path emits no vectors at all — but it already emits logprobs.**
   The live llama-server on Isis returns `501 This server does not support
   embeddings` (grade **A**, §2), so not even final-layer embeddings are
   reachable today, let alone per-layer residuals. The same endpoint *does*
   return per-token `logprob` and `top_logprobs` on request, at zero extra cost
   and with no fork (grade **A**, §7). If a cheap uncertainty signal can separate
   good local-rung answers from bad ones, we get the ticket's stated benefit
   without touching activations. That test has never been run. It should be run
   before anything in §5 is budgeted.

2. **The probe can only watch the rung that does not act.** Activation probes
   need open weights. The only open weights we run are `qwen3.6:35b-a3b` and
   `qwen3:32b` on Isis (grade **A**, §1). That rung is `trust: "mechanical-edit
   only, gated. Offline-survival tier."` (`research/models.yaml:188`) and carries
   $0 of fleet spend (`FLEET_COST_PLAN.md:177`). The workers that write code,
   land PRs and close tickets are Claude — closed weights, no activations, ever.
   So "detect hallucination/off-task before a worker acts" is structurally
   unreachable for every worker whose mistakes actually cost us.

3. **Two of the three models the ticket names are not local.** `glm-local` does
   not exist: `LOCAL_MODEL_STRATEGY.md:7` records that GLM-5.3-Flash cannot be
   self-hosted, and `glm-5.3-flash-zai` is a paid cloud API behind the gateway.
   No Gemma is deployed anywhere on the fleet — Gemma 4 26B-A4B on anubis is a
   *recommendation* (`LOCAL_MODEL_STRATEGY.md:18`), not a running service. The
   local open-weight surface is one box and two Qwen checkpoints.

What survives: a genuine, bounded value case in §6 — a probe as a *trust-raising*
device for the free rung, so `FLEET_COST_PLAN`'s three blocked routing moves can
land. Even that is third in line behind two deterministic bugs (§6.2).

---

## 1. What "local open-weight" means today

Live inventory, taken 2026-09-11 (grade **A**, direct HTTP reads):

| Fact | Value | Source |
|---|---|---|
| Server | `llama-server`, `build_info` **b1-96278e39f** | `:8095/props` |
| Served model | Ollama blob `sha256-f5ee307a…dcf2d` = `qwen3.6:35b-a3b` | `:8095/props`, `:11434/api/tags` |
| Served context | `n_ctx` **8192** (native 262144) | `:8095/props` |
| Slots | `total_slots` **2** | `:8095/props` |
| Capabilities | `["completion"]`; `modalities.vision: false` | `:8095/v1/models`, `/props` |
| Ollama catalogue | `qwen3.6:35b-a3b` (36.0B, Q4_K_M), `qwen3:32b` (32.8B, Q4_K_M) | `:11434/api/tags` |

Model geometry, read from the GGUF metadata via `/api/show` (grade **A**) — this
is what a probe would actually consume:

| `qwen35moe` key | Value |
|---|---|
| `block_count` | **40** |
| `embedding_length` | **2048** |
| `attention.head_count` | 16 |
| `expert_count` / `expert_used_count` | **256 / 8** |
| `context_length` | 262144 |
| `vision.block_count` / `vision.embedding_length` | 27 / 1152 (tower present, not served) |

So the probe target is a 40-layer, 2048-wide residual stream on a sparse MoE at
Q4_K_M. Three consequences:

- **A linear probe is trivially small.** 2048 weights + bias per layer. Training
  is seconds of CPU. The probe is never the expensive part; the extraction is.
- **Capture volume is modest.** One token position, all 40 layers, f32 =
  40 × 2048 × 4 = **320 KiB**. A 1,000-example corpus is ~313 MiB for every
  layer, or ~8 MiB if you commit to one layer up front.
- **The states are the quantised model's states** (grade **C**). llama.cpp keeps
  the residual in f32/f16 while weights stay Q4_K_M, so the activations are
  real-valued and probe-able, but a probe fitted on them is fitted to *this
  quant of this build*. Requantising or changing `build_info` invalidates it,
  and nothing in the fleet pins either.

MoE caveat (grade **C**): the residual stream is dense, so probing is unaffected
in principle, but with 8 of 256 experts active the same residual position is
produced by different expert subsets across inputs, which adds noise a dense
model would not have. No measurement here either way.

---

## 2. The serving path emits nothing to probe

```
POST :8095/embeddings {"input":"probe"}
→ 501 {"error":{"message":"This server does not support embeddings.
        Start it with `--embeddings`","type":"not_supported_error"}}
```

Grade **A**, live. Three things follow.

1. **Today the number of vectors our serving path can produce is zero.** Not
   reduced, not pooled — none.
2. **`--embeddings` would not fix it.** That flag yields final-layer embeddings
   (pooled, or per-token with pooling `none`). Hidden *intermediate* layers —
   the thing every probe in the literature reads — have no endpoint in
   llama-server at any flag setting (grade **C**; verification command in §5.3).
3. **The write side already exists, the read side does not.** llama.cpp supports
   applying per-layer steering vectors at serve time (`--control-vector`), and
   ships a `cvector-generator` tool that must compute per-layer hidden states to
   build them (grade **C**). The machinery to *push* a direction into layer *k*
   is in the binary we run; the machinery to *read* layer *k* out over HTTP is
   not. That asymmetry is the whole engineering gap.

---

## 3. What extraction costs on the hardware we have

There is no GPU anywhere on this fleet. Isis is a Mac Pro 6,1 — E5-2697 v2,
**AVX only, no AVX2, no FMA**, ~39 GB/s streaming
(`LOCAL_MODEL_STRATEGY.md:52`). The hapi blade is unbuilt and Horus unbought.
Everything below is CPU.

Measured for this doc (grade **A**), 1,001-token prompt, `n_predict: 1`,
`cache_prompt: false`, against the live service:

```
prompt_n 1001 · prompt_ms 51573.98 · prompt_per_token_ms 51.52
prompt_per_second 19.41 · wall 59.7 s
```

A second run from `research/activation_probe_feasibility.py` (the reproduction
script for this doc, which re-issues all four reads) measured 19.14 tok/s /
52.24 ms per token — same box, minutes apart, so the number is stable.

19.41 tok/s prefill independently corroborates wq#1588's 17–20 tok/s
(`ops/wq1623-qwen36-queue-guard/README.md:4-6`) on a box whose load average was
20.06 at that measurement. Prefill is the dominant term for activation work,
because extraction is a forward pass you pay for and then throw the tokens away.

| Task | Arithmetic | Cost |
|---|---|---|
| One 300-token training item | 300 × 51.52 ms | **15.5 s** |
| 1,000-item corpus (prompt only) | 1,000 × 15.5 s | **4.3 h** exclusive Isis |
| + generating each answer to label it | ~100 tok @ 14–20 tok/s (est) | +~1.5 h |
| **Shadow pass over one real worker prompt** | 4,910 × 51.52 ms | **253 s** |

That last row is the one that kills the live-gate framing. wq#1588 measured the
same shape at 277 s. A gate that costs four minutes of a saturated shared box
cannot sit in front of a worker action; callers already cancel at 40–160 s
(`research/models.yaml:180-183`).

Note the shape of the cost, because it is counter-intuitive: **inline probing
would be nearly free, and only the plumbing is expensive.** Copying 40 × 2048
floats out of a graph that is already being evaluated is ~320 KiB of memcpy per
probed position — nothing, against 51 ms/token of compute. The 253 s is the cost
of doing the forward pass *a second time* because the first one threw the states
away. So the real price of activation probing here is not FLOPs; it is owning a
patch to a fast-moving upstream (`b1-96278e39f`) forever, on the one box that is
already the fleet's capacity bottleneck.

---

## 4. What a probe could detect, and the honest ceiling

**This section is grade C and thinner than it should be.** `WebSearch` and
`WebFetch` are both ungranted in `harness/worker-settings.json` (already logged,
ISSUE-LOG #321), so no paper could be retrieved or checked. What follows is
recollection, offered as a map of where to look, not as citable numbers. Anyone
acting on it must re-verify against the sources first.

The family the ticket is reaching for:

- **Truthfulness / "does the model know it's wrong" probes** — Azaria & Mitchell
  (SAPLMA), Burns et al. (CCS), Marks & Tegmark (geometry of truth). A linear
  read on the residual stream separates statements the model internally
  "believes" from ones it does not.
- **Error-detection probes on generated answers** — Orgad et al., *LLMs Know More
  Than They Show*. The relevant result for us is the negative one: these probes
  are strongly task-specific and transfer poorly across task distributions.
- **Uncertainty probes** — Kossen et al., semantic-entropy probes: a probe fitted
  to predict an expensive semantic-entropy score from activations, i.e. a cheap
  approximation of a sampling-based uncertainty measure.
- **Direction-level control** — Li et al. (inference-time intervention), Arditi
  et al. (refusal direction). These are the steering side, and the side llama.cpp
  already supports.

Recollected ceiling, **unverified**: in-distribution AUROC lands roughly 0.7–0.9,
and cross-task transfer degrades toward chance. Two structural limits matter more
than any number:

1. **A probe detects the model's own internal signal about its own likely
   wrongness.** It cannot detect wrongness the model has no internal
   representation of — a confidently-held false belief about our repo layout
   reads as clean.
2. **"Off-task" is the easier half, and activations are the expensive way to get
   it.** Whether a response is about the ticket is a semantic-similarity
   question. An embedding comparison — or simply the ticket text and the
   response in one cheap judge call — answers it without any interpretability
   apparatus. If the deliverable we want is off-task detection, this ticket's
   whole approach is the wrong instrument.

---

## 5. What building it would actually take

### 5.1 The training corpus we do not have

A probe needs labelled (prompt → response → correct/incorrect) pairs *from the
model being probed*. Counted from the live queue (grade **A**,
`/home/lumen/agents/work-queue/queue.db`, read-only):

| Signal | Count |
|---|---|
| Tickets total | 1,774 |
| `review_verdict` populated | **0** (NULL for all 1,774) |
| `status = done` | 1,561 |
| `reopened_count > 0` | 31 |
| `status = rejected` | 5 |
| `status = blocked` (with `block_reason`) | 77 |
| `FALSE_COMPLETE` mentions in `kb/ISSUE-LOG.md` | 9 |

The `review_verdict` column exists in the schema and has never been written to.
The strongest wrongness labels the fleet owns are 31 reopens plus 5 rejects plus
9 logged false-completes — order 10¹, where a probe wants 10³. Worse, those
labels are at **ticket** granularity, not response granularity, and they describe
**Claude** workers' output, not the local model's. They are the wrong labels, at
the wrong resolution, in the wrong quantity.

Generating a proper corpus means running the local rung over a few thousand
prompts and labelling its answers — 4–6 h of exclusive Isis time by §3, on a box
that is co-tenant with LPP and already queue-starved.

### 5.2 The extraction path, ranked

| # | Path | Viability |
|---|---|---|
| 1 | **Patch llama-server to emit per-layer residuals** on an opt-in field | Best long-run shape: marginal compute ~0 (§3). Cost is a permanent fork of `b1-96278e39f` on the bottleneck box. |
| 2 | **Offline extraction with a separate llama.cpp process** (eval-callback / cvector-generator lineage) | Viable for *training* only — same GGUF, same quant, no serving change. 4.3 h/1k items. Cannot gate a live request. |
| 3 | **HF transformers, `output_hidden_states=True`** | Blocked on RAM for the model we serve: 36B at bf16 ≈ 72 GB against 64 GB on every node. 8-bit ≈ 36 GB fits Lumen but CPU int8 matmul is slow and unbenchmarked here. |
| 4 | Shadow pass over each live request | Dead: 253 s per gate (§3). |

Path 3 has a trap worth naming: the only models small enough to instrument
comfortably in transformers (e.g. a ~8B Gemma 4 E4B, bf16 ≈16 GB) are **not the
model we serve**, so a probe trained there says nothing about the rung that
answers our traffic. The easy model to instrument and the model we actually run
are disjoint sets.

### 5.3 Verification a follow-up ticket must do first

These are grade **C** claims in this doc that a session with source access should
settle before any budget is committed:

- Clone llama.cpp at `b1-96278e39f`; confirm whether `tools/server` has any
  hidden-state path, and whether `--embeddings` and completion can coexist in
  one process at this build.
- Confirm the eval-callback / cvector-generator tooling still exists at this
  build and what per-layer output format it produces.
- Re-verify every claim in §4 against the actual papers (needs `WebSearch`, or a
  session outside the worker sandbox).

---

## 6. Where the value actually is, and what outranks it

### 6.1 The one real case

Not "guard the workers" — guard the *rung*. `qwen36-local` is gated to
mechanical-edit because we have no way to tell when its output is bad, and
`FLEET_COST_PLAN.md:221-228` has three routing moves (lumen-agent's output-heavy
leg, the minerva verify judge, sia-news) parked on that rung becoming
trustworthy. A working confidence signal is a trust-raising device, and its
payoff is those moves landing. That is a real, bounded, fleet-specific benefit.

### 6.2 Two deterministic bugs outrank it

The local rung is not currently untrusted because its answers are subtly wrong.
It is untrusted because it does not work:

- **D2/D3** (`FLEET_COST_PLAN.md:108-120`): a circular fallback
  `qwen36-local ↔ cerebras-oss-120b` with 33,292 recorded failures, where the
  failures are content-type rejections (`image_url` unsupported,
  `reasoning_effort` schema), not quality problems.
- **wq#1623**: queue starvation — small-prompt-only, Isis not dedicated.

Both are known bugs with known fixes. A probe is a research bet with an
unverified ceiling. Fixing a crash before instrumenting the crash is the ordering.

### 6.3 It also competes with gates that already work

The fleet's existing pre-action machinery is deterministic and cheap:
`harness/acceptance.py` replays criteria as commands and diffs the result;
`harness/guard.sh` denies dangerous tool calls outright;
`harness/fire_gate.py:1-14` holds risky actions for signed human approval. Each
produces evidence. A probe produces a probability, on the rung that acts least.
It belongs behind all three, not in front of them.

---

## 7. The cheap experiment that should run first

The serving path already returns calibrated per-token uncertainty. Live, this
doc, grade **A** — `POST :8095/completion` with `n_probs: 4`:

```json
{"token":" Paris","logprob":-0.7249,
 "top_logprobs":[{"token":" Paris","logprob":-0.7249},
                 {"token":" a","logprob":-2.0890},
                 {"token":" the","logprob":-2.8398}, …]}
```

No fork, no second forward pass, no extra latency, no corpus of activations. This
is the 80% signal, available today, that the activation work would have to beat.

**E1 — logprob separation test.** One day of work, no service change.

- Collect ~200 local-rung responses on real fleet-shaped prompts, with
  `n_probs` on; hand-label correct/incorrect.
- Score each with mean token logprob, min-window logprob, and predictive entropy.
- Compute AUROC against the labels.

Stop rule, fixed in advance:

| Result | Action |
|---|---|
| AUROC < 0.65 | **Stop the whole line.** If the model's own logits cannot separate its errors, a linear read of its residuals is very unlikely to, and nothing in §5 is worth 4+ hours of Isis plus a permanent fork. |
| AUROC ≥ 0.75 | **Ship the cheap gate.** Threshold on the logprob score, raise the rung's trust, land the §6.1 routing moves. Activation probes become unnecessary. |
| 0.65 ≤ AUROC < 0.75 | The only outcome that justifies §5 — and only then, and only after §6.2. |

E1 is prerequisite on the rung being serviceable enough to answer 200 prompts,
i.e. on D2/D3 and wq#1623. That is the correct dependency order, and it is the
recommendation: **close this ticket as researched, file E1 behind the rung fixes,
do not fund an extraction pipeline.**

---

## 8. What would change the verdict

- **A GPU lands** (hapi blade built, wq#120; or Horus bought, wq#265). Prefill
  stops being 51 ms/token and the shadow-pass path in §5.2 row 4 becomes
  arguable. Nothing else in this doc changes.
- **We start running an open-weight model on the path that acts** — an
  open-weights coding worker rather than Claude. Then finding 2 inverts and
  probing becomes worth real investment.
- **`review_verdict` starts being written.** 1,774 tickets of response-level
  correctness labels is the asset that makes every option in §5 cheaper. It costs
  a column write, not a research programme, and it is worth doing on its own
  merits regardless of this ticket.
- **E1 lands in the 0.65–0.75 band.** Then, and only then, §5.3's verification
  work is justified.

---

## 9. Sources

Every grade-**A** row below is reproduced by
`research/activation_probe_feasibility.py` (read-only HTTP, ~60 s, costs one
prefill on Isis).

| Claim | Source |
|---|---|
| llama-server build, model blob, `n_ctx` 8192, 2 slots, vision false | live `GET :8095/props`, `/v1/models`, 2026-09-11 (**A**) |
| `/embeddings` → 501 not supported | live `POST :8095/embeddings` (**A**) |
| per-token logprob + top_logprobs available | live `POST :8095/completion` with `n_probs` (**A**) |
| prefill 19.41 tok/s, 51.52 ms/token, 1,001-token prompt | live timed `/completion`, `cache_prompt:false` (**A**) |
| 40 layers, d_model 2048, 256/8 experts, arch `qwen35moe` | live `POST :11434/api/show` GGUF metadata (**A**) |
| local catalogue = `qwen3.6:35b-a3b`, `qwen3:32b`, both Q4_K_M | live `GET :11434/api/tags` (**A**) |
| 1,774 tickets, `review_verdict` NULL ×1,774, 31 reopened, 5 rejected | `queue.db` read-only query (**A**) |
| 9 `FALSE_COMPLETE` rows | `kb/ISSUE-LOG.md` (**A**) |
| rung trust = mechanical-edit only; 40–160 s caller timeouts | `research/models.yaml:180-188` (**B**) |
| `qwen36-local` spend $0; D2/D3 circular fallback, 33,292 failures | `FLEET_COST_PLAN.md:108-120`, `:177`, `:221-228` (**B**) |
| Isis = AVX-only E5-2697 v2, ~39 GB/s; no GPU on the fleet | `LOCAL_MODEL_STRATEGY.md:44-58` (**B**) |
| GLM-5.3-Flash cannot self-host; Gemma 4 is proposed, not deployed | `LOCAL_MODEL_STRATEGY.md:7`, `:18` (**B**) |
| Isis 4,910-token prompt = 277 s prefill, load 20.06 | `ops/wq1623-qwen36-queue-guard/README.md:4-6` (**B**) |
| existing pre-action gates | `harness/acceptance.py`, `harness/guard.sh`, `harness/fire_gate.py:1-14` (**B**) |
| llama.cpp internals: eval-callback, cvector-generator, control-vector apply | recollection, **C** — §5.3 says how to settle it |
| interpretability literature and AUROC ranges | recollection, **C** — no web access, ISSUE-LOG #321 |

**Confidence.** §1–§3 and §5.1 are live measurements taken today and are solid;
re-running the four HTTP calls reproduces them. §6 rests on other docs' grade-A
and grade-B work. §4 and §5.2's llama.cpp internals are the weak part, and are
labelled as such — the verdict does not depend on them, because §6.2 and §7 would
push this work behind other work even if every grade-C claim resolved favourably.
