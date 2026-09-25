# PILOT dab847127347 — Tune KV-cache settings for local long-context models in LiteLLM/Ollama

Experiment card. Generated 2026-09-22T09:12:18+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `dab847127347` |
| Deferred | 2026-08-14 |
| Impact / effort / risk | M / S / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/dab847127347/REPORT.md` |

## Why this is a pilot and not a research prompt

The paper's findings are not yet validated on Lumen's specific hardware and models; defer until a concrete benchmark on local models is run to confirm benefits.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner (wq#2432) BEFORE any scored call; the card was generated blank.)

On hathor's CPU, serving llama3.1:8b with flash attention and an 8-bit KV cache (`OLLAMA_KV_CACHE_TYPE=q8_0`) gives at least 10% higher prompt-processing throughput at a 4k-token context than the current default, with no loss on a 5-question needle check.

## Primary metric

Median prefill throughput (prompt tokens per second, from ollama's `prompt_eval_count / prompt_eval_duration`) over 3 interleaved rounds, q8_0 arm divided by default arm.
Verdict rule, fixed now: ADOPT if ratio >= 1.10 and q8_0 needle accuracy >= default. SKIP if ratio <= 0.90 or accuracy is lower. WATCH otherwise. Also recorded: decode tokens/s, the KV buffer size from the server log, a q4_0 arm, and how many cores the production ollama used during each run.

## Fixed sample

One synthetic document built by `run_pilot.py` from `random.Random(1234)`: about 4,000 tokens of filler sentences with 5 planted facts ("The access code for vault N is CODE.") at 10/30/50/70/90% depth, plus 5 fixed questions, one per fact. Same text for every arm and round; temperature 0, seed 1, `num_ctx` 8192, `num_predict` 16. The first question pays the full prefill; the other four reuse the cached prefix and only supply the accuracy check.

## Baseline (measure FIRST)

Arm A, production-equivalent: a second ollama 0.30.7 instance as user `hathor` on 127.0.0.1:11435 with the same `OLLAMA_NUM_PARALLEL` (1 here), no `OLLAMA_FLASH_ATTENTION` and no `OLLAMA_KV_CACHE_TYPE` set, model `llama3.1:8b` read from the shared models directory. Each round runs A first, then B, then C.

## Treatment

Arm B: `OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0`. Arm C: same with `q4_0`. Nothing is changed on the production ollama service (port 11434), no config file is edited, and the second instance is stopped after each arm. Contamination: the harness reads the production `llama-server` CPU seconds before and after each run, and flags runs where it averaged more than 0.5 cores.

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

Lumen serves local models (e.g., qwen36-local, gemma4-e4b-local, kimik3-0.4b-local) via LiteLLM gateway (localhost:4000) with default KV-cache settings; no explicit tuning for long-context performance.

## Proposal — proposed change

Apply findings from the paper to adjust KV-cache parameters (e.g., cache size, quantization, eviction policy) in the LiteLLM/Ollama configuration files (e.g., /etc/litellm/config.yaml or Ollama's modelfile) for local models, focusing on long-context serving.

---

The report at `/home/lumen/foreman/pilots/dab847127347/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
