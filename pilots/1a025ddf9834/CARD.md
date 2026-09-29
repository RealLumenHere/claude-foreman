# PILOT 1a025ddf9834 — Implement hybrid autoregressive-speculative inference for local models

Experiment card. Generated 2026-09-17T09:14:18+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `1a025ddf9834` |
| Deferred | 2026-08-10 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/1a025ddf9834/REPORT.md` |

## Why this is a pilot and not a research prompt

Uncertain applicability to Lumen's specific hardware and model mix; requires validation that speculative decoding yields latency gains without degrading quality, and existing eval harnesses (pelican_bench, model_audit) should first measure baseline.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

Sequence-level draft-then-verify (lumen-lfm2-1b drafts, qwen36-local verifies/corrects
in one pass) reduces mean wall-clock latency by at least 20% vs. qwen36-local answering
directly, at exact-match quality parity (>=80% exact text match vs. baseline).

## Primary metric

Mean wall-clock latency per request, in seconds, for treatment vs. baseline over the
fixed sample. Win = treatment_mean_s <= 0.8 * baseline_mean_s AND exact_match_rate >= 0.8.

## Fixed sample

`pilots/1a025ddf9834/prompts.json` (committed, 5 short single-turn prompts, this file's
git blob is the pin — no live/mutable source).

## Baseline (measure FIRST)

```
python3 pilots/1a025ddf9834/run_pilot.py
# baseline arm = spec_decode.baseline_call(): plain qwen36-local chat completion,
# no draft model involved.
```

## Treatment

```
python3 pilots/1a025ddf9834/run_pilot.py
# treatment arm = spec_decode.speculative_call(): lumen-lfm2-1b drafts a full
# candidate response, qwen36-local is asked once to accept it verbatim or correct it.
# True token-level speculative decoding needs per-token logit access, which LiteLLM's
# /chat/completions surface does not expose for these local models -- this is the
# closest speculative-style scheme reachable through the existing gateway.
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

Lumen currently routes all inference through LiteLLM (localhost:4000) with no speculative decoding; local models like lumen-lfm2-1b and qwen36-local run autoregressively, and latency is a known concern for edge queries.

## Proposal — proposed change

Add a speculative decoding wrapper (e.g., using a draft model like lumen-lfm2-1b to propose tokens for a larger target model like qwen36-local) integrated as a new LiteLLM custom handler or a separate proxy service, configurable per model.

---

The report at `/home/lumen/foreman/pilots/1a025ddf9834/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
