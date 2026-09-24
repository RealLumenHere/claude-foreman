# PILOT 4269f277733e — Add GLM-5.2 to LiteLLM gateway for local inference

Experiment card. Generated 2026-09-24T12:47:55+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `4269f277733e` |
| Deferred | 2026-08-10 |
| Impact / effort / risk | M / M / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/4269f277733e/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#1984, card /home/lumen/foreman/pilots/4269f277733e/CARD.md. Original: Adding a new model is low-risk but provides marginal value given existing local models; defer until a clear use case (e.g., benchmark shows GLM-5.2 outperforms current local models on Lumen's tasks) is demonstrated.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner before any scored call, per this card's "edit it before the run" clause: the card was generated blank.)

Serving GLM-5.2 through the LiteLLM gateway beats Lumen's current local rungs on Lumen's own tasks by a margin worth a new rung. Precondition: the model is not already in the gateway.

## Primary metric

Not reached. The precondition fails, see Baseline. Had it held: task-suite pass rate difference vs qwen36-local on the same sample, win >= +10 points.

## Fixed sample

None pinned: no benchmark was run. The comparison sets that already exist are wq#1573's `research/GLM53_FLASH_EVAL.md` (per-workload routing recommendation) and wq#1592's cache canary.

## Baseline (measure FIRST)

Precondition check, run first:

```
grep -c -i 'glm-5.2' /home/lumen/agent/litellm_config.yaml      # 0
grep -n 'model_name: glm' /home/lumen/agent/litellm_config.yaml # glm-5.3-flash (OpenRouter), glm-5.3-flash-zai (Z.ai direct), glm-5.3-flash-local (ollama GGUF Q4_K_M)
```

## Treatment

Not built. The proposal targets a model generation the gateway already moved past.

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

Lumen currently routes all inference through LiteLLM (localhost:4000) with a fixed set of models; no GLM-5.2 is available. Local models are served via LiteLLM (e.g., qwen36-local, lumen-lfm2.5-2.6b).

## Proposal — proposed change

Integrate GLM-5.2 into the LiteLLM gateway by adding a new model entry pointing to a local HuggingFace Transformers endpoint (e.g., via vLLM or text-generation-inference) and updating the LiteLLM config (likely /data/lumen/litellm/config.yaml).

---

The report at `/home/lumen/foreman/pilots/4269f277733e/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
