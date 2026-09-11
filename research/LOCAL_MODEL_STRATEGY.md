# wq#1709 — Local model strategy for the fleet's actual hardware

Date 2026-09-11 · worker `fworker-wq1709` · supersedes nothing; extends
`reports/265_horus_epyc_brief.md`, `reports/hapi-blade-coding-model-20260709.md`,
`reports/gemma4_e4b_rung.md`, `MODEL_ROUTING_AUDIT.md` §3.

GLM-5.3-Flash can't self-host. This answers the follow-on: of the open 7–70B
models available in September 2026, which one serves each local rung, on which
box, at which quant, at what speed.

---

## 1. Verdict

| Rung | Model | Quant | Host | Gen tok/s | Status |
|---|---|---|---|---|---|
| **General local** (`qwen36-local` replacement) | Qwen3.6-35B-A3B | Q4_K_M, ~21.7 GB | **Lumen** (fallback Hathor) | **18–25** est | deploy now |
| **Judge** | Gemma 4 26B-A4B | Q4_K_M, ~15 GB | **anubis** | **8–11** est | deploy now |
| **LPP vision** | *(none — reuse the general rung's `mmproj`)* | mmproj-F16, +0.9 GB | same as general | n/a | deploy now |
| General, Hapi era | gpt-oss-20b | MXFP4 native, ~12 GB | one BC-250 blade | **66 / 87.5** measured | blocked, wq#120 |
| Judge, Horus era | Muse Glimmer 30B | Q4_K_M, ~17 GB | Horus | ~8 est | not bought, wq#265 |
| Quality tier, Horus era | Qwen3.5-122B-A10B | Q4_K_M, ~70 GB | Horus | **17–23** est | not bought, wq#265 |

Three headline findings, in order of how much they matter:

1. **The `qwen36-local` failure is a host problem, not a model problem.** Isis,
   Hathor and Khepri are Mac Pro 6,1s on `E5-2697 v2` — Ivy Bridge, which has
   AVX but **no AVX2 and no FMA**. llama.cpp's quantised dot products fall back
   to the 128-bit path there. No model in the 7–70B range rescues that host.
   §2 and §4.
2. **The three rungs collapse into two deployments.** 2026's open MoEs are
   natively multimodal. Qwen3.6-35B-A3B takes text, image and video with an
   `mmproj` sidecar, so the LPP vision rung is a 0.9 GB addition to the general
   rung rather than a third resident model. §5.3.
3. **Prompt eval, not generation, is what the fleet actually bleeds on**, and
   it is the number every "tok/s" headline omits. The one hard local
   measurement we have is a prompt-eval measurement: 277 s for a 4,910-token
   prompt. §4.

---

## 2. The hardware, as measured and inventoried

Peak GB/s is computed from the DIMM population recorded by the live-probe
reconciliation in `INVENTORY_RECONCILIATION.md` (grade A, lines 72–120).
"Stream" is the fraction llama.cpp actually achieves pulling weights, taken at
65% of peak — see §3 for why that constant and §7 for how to falsify it.

| Node | CPU | Vector ISA | Memory | Peak GB/s | Stream GB/s | RAM |
|---|---|---|---|---|---|---|
| **lumen** (T5810) | E5-2699 v4, 22C/44T | AVX2 + FMA, no AVX-512 | 4ch DDR4-2400 | 76.8 | **~50** | 64 |
| **isis / hathor / khepri** (Mac Pro 6,1) | E5-2697 v2, 12C/24T | **AVX only — no AVX2, no FMA** | 4ch DDR3-1866 | 59.7 | ~39 | 64 |
| **anubis** (OptiPlex 3090) | i5-1145G7, 4C/8T | **AVX-512 + VNNI** | 2ch DDR4-2400 | 38.4 | ~25 | 32 |
| **aten** | i5-8250U, 4C/8T | AVX2 + FMA | 2ch DDR4-2400 | 38.4 | ~25 | **8** |
| **gameboy** (Surface Pro 6) | i5-8350U, 4C/8T | AVX2 + FMA | 2ch LPDDR3-1867 | 29.9 | ~19 | **8** |
| **bes / maat** | N100, 4C/4T | AVX2 + FMA | **1ch** DDR4-3200 | 25.6 | ~15 | 16 |
| **apep** | N3350, 2C/2T | SSE4.2 only | 1ch DDR3L | ~12 | ~8 | **4** |
| **hapi blade** *(unbuilt)* | BC-250, gfx1013, 24–40 CU | Vulkan | 16 GB GDDR6 unified | ~118 | measured | 16 |
| **horus** *(unbought)* | EPYC 7543P, 32C | AVX2 + FMA | **8ch** DDR4-3200 | 204.8 | ~135 | 512 |

### 2.1 What was measured today

`research/tools/membw_probe.py` on Lumen, 2026-09-11:

```
workers=1  aggregate=17.2 GB/s
workers=4  aggregate=44.4 GB/s
workers=8  aggregate=25.9 GB/s
workers=12 aggregate=17.4 GB/s
```

The collapse past 4 workers is **not** the memory controller. The companion
`research/tools/cpu_scaling_probe.py` runs the same sweep with pure register
compute and no memory traffic:

```
workers=1  aggregate=19.0 Miter/s
workers=4  aggregate=74.4 Miter/s
workers=8  aggregate=74.6 Miter/s     <- flat, identical to 4
workers=16 aggregate=60.4 Miter/s
```

Compute flattens at exactly the same worker count, which means **the fworker
cgroup is CPU-capped at about 4 cores** and both curves are measuring the
sandbox, not the box. Logged as ISSUE-LOG row 315: any benchmark run from
inside a worker session underreports Lumen by roughly 5x, so nobody should
bench the inference stack from a worker.

What survives the caveat is a lower bound, and it is a useful one: **4 cores
alone moved 44.4 GB/s of copy traffic** (read + write, STREAM-Copy convention)
on a box whose quad-channel peak is 76.8 GB/s. Weight streaming is read-only,
which runs hotter than copy. ~50 GB/s for the full 22-core machine is a
conservative read of that, and §7 gives the command to settle it.

---

## 3. The arithmetic

Token generation on a CPU is bounded by bytes streamed per token, not FLOPs:

```
gen tok/s  ~=  stream GB/s  /  bytes-per-token
bytes-per-token  ~=  active_params x 0.58     (Q4_K_M, incl. quant metadata)
```

For an MoE only the routed experts are read, but attention and shared-expert
weights are read every token, which is why the figures below sit slightly above
`active x 0.58`.

| Model | Total | Active | B/token @ Q4_K_M |
|---|---|---|---|
| gpt-oss-20b (native MXFP4) | 21B | 3.6B | ~1.9 GB |
| Qwen3.6-35B-A3B | 35B | 3.3B | ~2.0 GB |
| Gemma 4 26B-A4B | 25.2B | 3.8B | ~2.3 GB |
| Qwen3.5-4B dense | 4B | 4B | ~2.4 GB |
| Gemma 4 E4B | ~8B loaded | ~4B eff | ~3.5 GB |
| Qwen3.5-9B dense | 9B | 9B | ~5.2 GB |
| Qwen3.5-122B-A10B | 122B | 10B | ~5.8 GB |
| Qwen3.5-27B dense | 27B | 27B | ~15.7 GB |
| Muse Glimmer 30B dense | 29.6B | 28B decoder | ~16.3 GB |

Sanity check against a public anchor: Qwen3.5-35B-A3B is reported at ~75 tok/s
on an M4 MacBook Pro. M4's ~120 GB/s unified memory streams at roughly 110
GB/s; 110 / 2.0 = 55, same order as the reported 75 with MTP in play. The model
is calibrated, not invented — but it is a model, and §7 says how to break it.

Cross-multiplying §2 and the table above:

| | lumen (50) | isis/hathor (39) | anubis (25) | bes (15) | horus (135) |
|---|---|---|---|---|---|
| Qwen3.6-35B-A3B Q4_K_M | **18–25** | 14–20 | 9–12 | *no fit* | **50–68** |
| Gemma 4 26B-A4B Q4_K_M | 16–22 | 12–17 | **8–11** | *no fit* | 44–59 |
| Qwen3.5-122B-A10B Q4_K_M | *no fit* | *no fit* | *no fit* | *no fit* | **17–23** |
| Muse Glimmer 30B Q4_K_M | 2.5–3.1 | 2.0–2.4 | *tight* | *no fit* | **7–8** |
| Gemma 4 E4B Q4_K_M | 12–14 | 9–11 | 6–7 | **3–4** | 35–39 |

Ranges are the ceiling discounted 20% for llama.cpp's MoE expert gather, which
is a scatter of small reads rather than a clean stream. *no fit* means weights
plus KV cache exceed the node's RAM.

---

## 4. Prompt eval is the real bottleneck

The fleet's only hard local measurement is not a generation number. From
`foreman/ops/wq1623-qwen36-queue-guard/README.md:4-6`, on Isis serving
`qwen3.6:35b-a3b`:

> a 4,910-token prompt (task 104125) spent **277 s in PROMPT EVAL alone** and
> was only 76% through, at 17–20 tok/s

17–20 tok/s of *prefill* on a 3.3B-active MoE is catastrophic — the Hapi
blades measure 181–248 tok/s of prefill on a comparable model. Two causes
compound:

1. **Co-tenancy.** `ollama.service` and `ollama-lpp.service` share the box;
   the service is queue-starved, not dead (`/health` ok, a 4-token prompt
   returns in 723 ms).
2. **The ISA.** `E5-2697 v2` is Ivy Bridge: AVX, no AVX2, no FMA. Prefill is
   compute-bound and quantised GEMM is exactly the kernel that loses most when
   it drops from 256-bit FMA to a 128-bit path.

This reorders the whole question. Agentic fleet work sends 5–50k-token prompts
and reads back a few hundred tokens; prefill dominates wall time. A model
recommendation that optimises generation tok/s and ignores prefill is
optimising the wrong number, which is why §5 puts Lumen ahead of Hathor even
though both are "just CPU boxes" — Lumen has AVX2+FMA and 22 cores against
Ivy Bridge's 12.

---

## 5. Per-rung recommendation

### 5.1 General local rung — `qwen36-local` replacement

**Model: Qwen3.6-35B-A3B · Q4_K_M (~21.7 GB) · 65K ctx · Apache-2.0.**

Keep the model family. It is already the fleet's choice, it is 3.3B active
(the cheapest bytes-per-token of anything with real capability), it is natively
multimodal, 256K context, 201 languages, and llama.cpp supports its MTP
multi-token-prediction head for a free generation speedup at byte-identical
output.

**Change the server: llama-server, not Ollama.** On the same model and
hardware, published comparisons put llama.cpp at 142/136/133 tok/s with 65k
context in 21.7 GB against Ollama's 98.9/99.0/96.3 at 32k in 23.4 GB — ~1.4x
throughput, 2x usable context, 1.7 GB less resident. The fleet is paying that
tax today for nothing.

**Change the host: Lumen, not Isis.** Expected 18–25 tok/s generation versus
Isis's 14–20 — a modest win. The real win is prefill: AVX2+FMA and 22 cores
against AVX-only and 12, on the workload §4 shows is the binding constraint.

Lumen is the orchestrator and is carrying 9 GB of swap today, so cap it:
`--threads 16` leaves 6 physical cores for dispatch, and `OOMScoreAdjust` plus
a `MemoryMax` on the unit keeps a runaway model from taking the gateway with
it. If that trade is judged unacceptable, the fallback is **Hathor** —
dedicated, 64 GB, nothing else on it — which buys the removal of co-tenancy
(cause 1 in §4) but not the ISA fix (cause 2). Hathor is strictly better than
Isis and strictly worse than Lumen.

Pull the `mmproj` sidecar with the weights. §5.3 depends on it.

### 5.2 Judge rung

**Model: Gemma 4 26B-A4B · Q4_K_M (~15 GB) · Apache-2.0 · host anubis.**

25.2B total / 3.8B active MoE, 30 layers, multimodal. Three reasons it is the
right judge specifically:

- **Cross-family.** `JUDGE_CONSTRUCT_GATE.md` §3 names position bias and
  same-family collusion as the dominant threats, and the shipped judge is
  unmeasured. Google weights judging DeepSeek, Qwen and Anthropic output is the
  cleanest independence the open ecosystem offers us.
- **Quality per streamed byte.** ~1441 LMArena at 3.8B active is the best
  ratio on this board; a judge needs calibration and instruction-following far
  more than world knowledge.
- **Multimodal**, so it can judge screenshot-bearing artifacts without a second
  deployment.

**Host anubis**, not Lumen: 32 GB is enough for Q4_K_M plus KV, it has AVX-512
+ VNNI (the best per-core prefill in the fleet), and it keeps judge load off
the orchestrator and off the general rung. At 8–11 tok/s a ~200-token verdict
lands in 20–25 s. That is fine for the 5% async sample
(`langfuse-ops/evaluator.py`) and **not** fine as an inline gate — do not wire
this into a blocking path.

**Alternative, parked:** Meta's **Muse Glimmer 30B** (Apache-2.0, dense 28B
decoder + 2B perception encoder) is built and benchmarked explicitly for local
agents, tool calling and LLM-as-a-judge, and understands screenshots natively.
But dense means 16.3 GB streamed per token: **2.5–3 tok/s on Lumen**. It is
not a today-rung. It becomes the judge the moment Horus exists (~8 tok/s), and
it is the model to reach for if Gemma 4 is measured and found to judge badly.

### 5.3 LPP vision rung

**Recommendation: do not deploy a vision model. Repoint `lumen-vision` at the
§5.1 server's `mmproj`.**

The load does not justify a third resident model. The valve
(`pokemon/src/vision.ts`, `pokemon/src/config.ts:93-97`) fires at most
`maxPerHour: 4`, and only after `stuckN: 3` consecutive stuck dumps at an
unchanged tile; `FLEET_COST_PLAN.md:217` measures 376 average input tokens. That
is ~1,600 tokens/hour against a rung sized for millions. LPP reads the Game
Boy's memory map, not pixels — vision is an escalation valve by design, and
`vision.ts:5-9` records why: every harness that actually beat Gen 1 got its
reliability from RAM/text, and Google's own Gemini 2.5 report found the pixel
channel performed "roughly as well as without the vision information."

Qwen3.6-35B-A3B is natively multimodal across text, image and video; the vision
path needs only the `mmproj-F16` sidecar, ~899 MB. Total marginal cost of this
rung: **0.9 GB of RAM and zero additional tok/s budget.**

Two caveats worth carrying:

- The Qwen3.6 GGUF repos shipped without `mmproj` at the April 2026 release and
  were filled in later, so **verify the chosen repo actually has it** before
  committing. If it does not, use Qwen3.5-35B-A3B + mmproj (unsloth publishes
  it) or point `lumen-vision` at the §5.2 Gemma 4 server, which is multimodal by
  construction.
- llama.cpp has a known interaction where MTP and image analysis conflict. If
  MTP is enabled per §5.1, bench an image request before trusting the path.

Explicitly rejected: standing up a dedicated small VLM (Qwen3-VL-8B, Moondream,
MiniCPM-V). At 4 calls/hour a third model is resident memory spent for no
latency benefit.

---

## 6. What changes when the hardware lands

### Hapi BC-250 blades — wq#120, blocked, "no hardware yet connected"

**Model: gpt-oss-20b, native MXFP4 (~12 GB), one blade.** Measured on a real
board: **66 tok/s gen at stock 24 CU, 87.5 at the 40-CU unlock; prefill 181 /
248 tok/s** (`reports/hapi-blade-coding-model-20260709.md`).

Prefill of 181–248 tok/s is **10–12x the Isis measurement in §4** — the only
apples-to-apples comparison available, since no CPU node has been benchmarked
for prefill on this model class. Given §4, moving the general rung onto a blade is the
single largest win available to the fleet — larger than any model swap in this
document. It is worth more than the Horus purchase per dollar.

Note the Hapi-era model is *not* the §5.1 model: Qwen3.6-35B-A3B at Q4 is
21.7 GB and does not fit the 16 GB unified pool. A blade runs it only at
IQ2_M/UD-Q2_K_XL, a real quality cliff. gpt-oss-20b at native 4-bit fidelity
with native tool calling is the better blade citizen; Qwen3-Coder-30B-A3B
IQ2_M (58.4 / 76.7 tok/s) is the code-only alternative.

### Horus EPYC — wq#265, not purchased

8ch DDR4-3200 at ~135 GB/s streaming and 512 GB:

- **Qwen3.6-35B-A3B: 50–68 tok/s** — 3x Lumen, and prefill scales with 32 Zen 3
  cores that all have AVX2+FMA.
- **Qwen3.5-122B-A10B Q4_K_M (~70 GB): 17–23 tok/s** — the first local model in
  fleet history that is plausibly a quality peer to the cheap-cloud rung. This
  is the capability the 512 GB actually buys, and it is a stronger argument for
  the purchase than the "DeepSeek-class at single-digit tok/s" framing in the
  original brief.
- **Muse Glimmer 30B dense: ~8 tok/s** — the judge upgrade from §5.2.

### Why 320B was never close

Independent of active-parameter count, a 320B model at Q4 is roughly 180 GB of
weights. The largest box in the fleet holds 64 GB. Horus as specced (512 GB) is
the only machine that could hold it, and it has not been bought.
The fleet's local ceiling is set by DDR4 channels, and no quantisation rescues a
model that needs more RAM than the fleet owns.

---

## 7. How to falsify this

Every tok/s in §3 and §5 is derived from a bandwidth model, not measured on the
target. One afternoon of benchmarking settles all of it. Do not run these from
inside an fworker session — §2.1 explains why the numbers will be wrong by ~5x.

**Step 1 — the bandwidth constant.** The whole model rests on "65% of peak."
Run a real STREAM, or `membw_probe.py` from a shell without the worker cgroup:

```
python3 research/tools/membw_probe.py --workers 22 --mb 512 --seconds 10
```

If Lumen comes in under ~40 GB/s, every §3 figure for Lumen is optimistic and
the §5.1 host recommendation should be re-argued on prefill alone.

**Step 2 — the actual model, both phases.** `pp` is the number that matters:

```
llama-bench -m Qwen3.6-35B-A3B-Q4_K_M.gguf -p 512,4096 -n 128 -t 16
```

Run it on Lumen and on Hathor. §5.1 predicts Lumen wins generation by ~25% and
prefill by considerably more; **if Lumen's prefill advantage is under 2x, the
AVX2 argument in §4 is overstated** and Hathor becomes the better host because
it costs the orchestrator nothing.

**Step 3 — the judge.** Run `JUDGE_CONSTRUCT_GATE.md` §5's pairwise harness with
`JUDGE_MODEL=gemma-4-26b-a4b` against the same 25 clear pairs already used for
`deepseek-v4-flash`. Same pairs, different model, one run. If Gemma 4's
agreement rate trails the cloud judge by more than a few points, escalate to
Muse Glimmer and accept the 2.5 tok/s.

**Step 4 — the vision claim.** Confirm the chosen Qwen3.6 GGUF repo publishes
`mmproj`, then send one Game Boy frame through the §5.1 server with MTP on. If
MTP and image input conflict, either disable MTP or fall back per §5.3.

---

## 8. Evidence

| Claim | Source |
|---|---|
| Lumen = E5-2699 v4 22C/44T, AVX2 only, 4x16 GB DDR4-2667 running 2400 | `INVENTORY_RECONCILIATION.md:74-75` (live probe, grade A) |
| Lumen has no AVX-512 and no VNNI | `HEADROOM_EVAL.md:83`, confirmed wq#569 |
| isis/hathor/khepri = Mac Pro 6,1, E5-2697 v2 12C/24T, 4x16 GB DDR3-1866 | `INVENTORY_RECONCILIATION.md:113-114` (hathor), `:123-124` (isis), `:131` (khepri, grade C — box is offline) |
| anubis = i5-1145G7 4C/8T AVX-512, 2x16 GB DDR4-2400 | `INVENTORY_RECONCILIATION.md:85-86` |
| bes/maat = N100, 1x16 GB DDR4-3200 single-channel | `INVENTORY_RECONCILIATION.md:97-98` |
| aten = i5-8250U, 8 GB | `INVENTORY_RECONCILIATION.md:209` |
| Isis prefill 17–20 tok/s; 4,910-token prompt = 277 s | `foreman/ops/wq1623-qwen36-queue-guard/README.md:4-6` |
| `qwen36-local` is Isis-hosted `qwen3.6:35b-a3b`, co-tenant with ollama-lpp | `research/models.yaml:163-186` |
| Lumen 4-core copy bandwidth 44.4 GB/s; worker cgroup capped ~4 cores | `research/tools/membw_probe.py`, `research/tools/cpu_scaling_probe.py`, run 2026-09-11 |
| BC-250: gpt-oss-20b MXFP4 66 / 87.5 tok/s gen, 181 / 248 prefill | `reports/hapi-blade-coding-model-20260709.md:32` |
| BC-250: Vulkan only, ROCm dead on gfx1013; 16 GB unified pool | `reports/bc250-llamacpp-vulkan-rocm-20260709.md:10-14` |
| Hapi has no hardware connected | `wq show 120` — status blocked, "no hardware yet connected" |
| Horus = EPYC 7543P, 8ch DDR4-3200 ~205 GB/s, 512 GB, not purchased | `reports/265_horus_epyc_brief.md` |
| LPP vision valve: stuckN 3, maxPerHour 4, escalation-only | `pokemon/src/config.ts:93-97`, `pokemon/src/vision.ts:1-27` |
| LPP vision averages 376 input tokens | `FLEET_COST_PLAN.md:217` |
| Judge is unmeasured; position bias is the dominant threat | `JUDGE_CONSTRUCT_GATE.md:54,73-74,94,113-115` |
| Qwen3.5 family: 0.8B/2B/4B/9B/27B dense, 35B-A3B, 122B-A10B MoE | vendor + Unsloth model cards, retrieved 2026-09-11 |
| Qwen3.6-35B-A3B natively multimodal, 256K ctx, mmproj required for vision | Unsloth/Qwen model cards, retrieved 2026-09-11 |
| llama.cpp 142/136/133 tok/s @65k/21.7 GB vs Ollama 98.9/99.0/96.3 @32k/23.4 GB | published comparison, retrieved 2026-09-11 |
| Gemma 4 26B-A4B: 25.2B total, 3.8B active, 30 layers, multimodal, Apache-2.0 | Google model card + Gemma 4 technical report, retrieved 2026-09-11 |
| Muse Glimmer 30B: Apache-2.0, dense, 2B perception encoder + 28B decoder, judge-targeted | Meta model card + release post, retrieved 2026-09-11 |

**Confidence.** §2 hardware and §4's Isis measurement are solid — live probes
and a recorded production incident. §3's bandwidth constant and every tok/s
range derived from it are **estimates with a stated method and a falsification
command**; treat them as ±30% until §7 step 1 runs. The 2026 model facts come
from vendor cards retrieved today, not from memory. Nothing here was benchmarked
on the target models, and §7 exists so that gap is closed rather than forgotten.
