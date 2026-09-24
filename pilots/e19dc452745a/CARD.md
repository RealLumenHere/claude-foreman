# PILOT e19dc452745a — Prototype attention-only transformer layer for local models

Experiment card. Generated 2026-09-21T09:11:36+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `e19dc452745a` |
| Deferred | 2026-08-13 |
| Impact / effort / risk | M / L / S |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/e19dc452745a/REPORT.md` |

## Why this is a pilot and not a research prompt

The paper is theoretical and not directly applicable to Lumen's current architecture; prototyping would require significant effort with uncertain benefit, and the existing benchmark harnesses already cover model evaluation.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner (wq#2421) after two precondition probes; the card was generated blank. No arm was scored.)

A minimal attention-only layer, built as a standalone module, can be scored by Lumen's `pelican_bench` battery within 0.10 mean score of `qwen36-local` (0.955). Precondition: the harness can score a bare layer at all.

## Primary metric

Mean `pelican_bench` score over its 4 fixed probes (0.0-1.0, unitless). Win: treatment >= baseline - 0.10, i.e. >= 0.855. Not reached: the precondition fails, see Baseline.

## Fixed sample

The 4 fixed probes in `/home/lumen/minerva/pelican_bench.py` (`pelican_svg`, `json_extract`, `code_fib`, `exact_word`), deterministic scorers, no sampling. Baseline rows come from `/home/lumen/minerva/pelican_bench.db` (8 runs, 68 results).

## Baseline (measure FIRST)

Read from the existing store, not re-run (no served-model call was needed to answer the precondition):

```
qwen36-local        mean 0.955  (28 results)
isis-qwen3-32b      mean 0.963  (32 results)
nephthys-qwen3-14b  mean 0.875  (8 results)
```

Precondition check: every probe is a prompt sent to an OpenAI-compatible endpoint (`call(model, prompt, key)` against LiteLLM `127.0.0.1:4000`) and the reply text is scored. A bare attention layer has no tokenizer, no embeddings, no LM head and no weights that produce text, so there is nothing to serve at that endpoint.

## Treatment

Not built. To get any pelican score the layer would first have to be embedded in a trained language model, and no such model can be trained inside the 90 min / 150k-token budget on hathor (24 threads, AVX only, no numpy, no torch, no pip3 on PATH; python 3.14.4).

Measured scorer floor for a system that emits no usable text: `pelican_svg` 0.0, `json_extract` 0.0, `exact_word` 0.0, `code_fib` 0.25 on empty output (0.0 on random text), so the mean is 0.0625 empty / 0.0 random.

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

Lumen currently uses standard transformer backbones for local models (e.g., gemma4-e4b-local, qwen36-local) served via LiteLLM; no attention-only variants are in use.

## Proposal — proposed change

Implement a minimal attention-only transformer layer as a standalone Python module and benchmark it against existing local models using the existing pelican_bench harness. No changes to production services.

---

The report at `/home/lumen/foreman/pilots/e19dc452745a/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
