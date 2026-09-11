# PILOT 2c11123fab8b — Integrate Adaptive KV Cache Filtering into Local LLM Inference

Experiment card. Generated 2026-09-10T09:14:15+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `2c11123fab8b` |
| Deferred | 2026-07-27 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/2c11123fab8b/REPORT.md` |

## Why this is a pilot and not a research prompt

Unclear integration path (LiteLLM gateway cannot directly modify per-model KV cache); Lumen lacks an experiment/eval harness to measure memory/speed improvements; primary inference uses external APIs where this technique does not apply. Defer until a proven, drop-in implementation exists and an eval harness is built.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

Adaptive KV cache filtering (the paper's technique) can be enabled as a drop-in
change on Lumen's actual local-inference path (LiteLLM -> Ollama -> llama.cpp,
serving qwen3.6:35b-a3b at `http://100.100.129.24:11434`, per
`foreman/ops/wq1623-qwen36-queue-guard/litellm_config_snippet.yaml:19-21`) without
forking or rebuilding the inference engine, and doing so cuts eval-phase latency
on a fixed prompt sample by >=20% at equal output quality.

## Primary metric

Wall-clock eval latency in ms/token (`eval_duration / eval_count` from the Ollama
`/api/generate` response) on the fixed sample below. Win = treatment >=20% lower
ms/token than baseline at the same `num_predict` and unchanged `done_reason`.
Before running: this metric is void if there is no way to apply the treatment to
this serving path at all (see Treatment) — that outcome is scored on
buildability, not on a fabricated number.

## Fixed sample

One prompt, fixed verbatim, `num_predict: 40`, against `qwen3.6:35b-a3b`:
`"Summarize in one sentence: the sky is blue because of Rayleigh scattering."`
Both arms would hit this same prompt/model/num_predict; recorded verbatim above
so it does not drift.

## Baseline (measure FIRST)

```
curl -s -m 150 http://100.100.129.24:11434/api/generate \
  -d '{"model":"qwen3.6:35b-a3b","prompt":"Summarize in one sentence: the sky is blue because of Rayleigh scattering.","stream":false,"options":{"num_predict":40}}'
```

## Treatment

```
# No command: see REPORT.md "Treatment — not buildable". Ollama's /api/generate
# `options` schema (num_keep, num_predict, top_k/top_p, temperature, mirostat,
# num_ctx, num_batch, num_gpu, num_thread, ...) has no parameter that filters,
# prunes, or adaptively evicts KV cache entries per-token; that logic lives
# inside Ollama's compiled llama.cpp runtime, which this pilot lane has no
# access to patch or rebuild on a node that also serves live production traffic
# (qwen36-local / qwen36-live, per the same litellm_config_snippet.yaml).
```

## Kill criteria

<!-- When to stop early WITHOUT asking permission. Stopping on a kill criterion is a
successful pilot whose answer is SKIP or WATCH, not a failed one. -->

- Wall-clock exceeds 90 min -> stop, report what was measured.
- Baseline does not reproduce -> stop, report the baseline defect. Do not proceed.
- Treatment incomplete at budget -> report partial numbers, RECOMMENDATION: WATCH.

## Data handling

`/home/lumen/minerva` is not a broker read root (credentials are mirrored into its
context sections) and `minerva.db` is never copied to a pilot node. A pilot that
needs fleet data takes a purpose-built extract of only the columns it needs, written
on Lumen, and the extract is listed here.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod. An ADOPT verdict feeds minerva-factory PLAN
like any other accept and is built through the normal path, with its own review.

## Proposal — current state

Lumen serves local models (e.g., qwen36-local, gemma4-e4b-local) via LiteLLM gateway (localhost:4000). No adaptive KV cache filtering is applied; inference uses default KV cache management of the underlying engine (likely vLLM or llama.cpp, though not explicitly named).

## Proposal — proposed change

Implement adaptive KV cache filtering (from the paper) in the local inference engine used by LiteLLM for local models. This would require either patching vLLM/llama.cpp or adding a custom middleware that intercepts the KV cache logic, then updating LiteLLM's local model configuration to enable the filter.

---

The report at `/home/lumen/foreman/pilots/2c11123fab8b/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
