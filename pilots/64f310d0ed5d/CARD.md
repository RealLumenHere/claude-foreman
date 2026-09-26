# PILOT 64f310d0ed5d — Implement RL-Enhanced Search Query Optimization for Minerva Verification

Experiment card. Generated 2026-09-25T09:12:55+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `64f310d0ed5d` |
| Deferred | 2026-08-26 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/64f310d0ed5d/REPORT.md` |

## Why this is a pilot and not a research prompt

The RL approach is promising but requires a clear reward signal and evaluation harness; current verification accuracy metrics are not yet robust enough to serve as a reliable reward. Defer until we can define a concrete reward function and measure baseline performance.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

A single LLM-rewritten query (entity/date-qualified expansion of the original
recall query, via deepseek-v4-flash on LiteLLM) beats the verbatim query on
R@1 against the frozen golden set by at least 3 points, with no drop in trap@1.

## Primary metric

R@1 (retrieval-only headline, per wq#595 scoring semantics) on
eval/golden_v4_frozen_20260814.jsonl, sha256 pinned per HANDOFF (8ba5afb0...).
Win = treatment R@1 - baseline R@1 >= +0.03 AND treatment trap@1 <= baseline trap@1.

## Fixed sample

eval/golden_v4_frozen_20260814.jsonl (0444, sha256 8ba5afb0ce12585606b8dcff5ffb8245af24b0f0f32ccbddf3d4121f1bbf46b7) — same frozen set sched_baseline.json uses. No new data pulled; this pilot is pure backtest re-run through the existing harness.

## Baseline (measure FIRST)

```
cd /home/lumen/minerva/v4 && python3 eval/recall_bench.py --golden eval/golden_v4_frozen_20260814.jsonl --engine v3 --out /home/lumen/foreman/pilots/64f310d0ed5d/baseline.json
```
(sched_baseline.json already IS this exact run — reuse it if fresh, else re-run to confirm no drift before treatment.)

## Treatment

```
python3 /home/lumen/foreman/pilots/64f310d0ed5d/rewrite_queries.py \
  --golden eval/golden_v4_frozen_20260814.jsonl \
  --model deepseek-v4-flash --via litellm:localhost:4000 \
  --out /home/lumen/foreman/pilots/64f310d0ed5d/golden_rewritten.jsonl
python3 eval/recall_bench.py --golden /home/lumen/foreman/pilots/64f310d0ed5d/golden_rewritten.jsonl \
  --engine v3 --out /home/lumen/foreman/pilots/64f310d0ed5d/treatment.json
```
rewrite_queries.py does not exist yet — write it as a ~30-line script: for each
golden row, prompt deepseek-v4-flash "expand this query with likely entity/date
qualifiers, return ONLY the rewritten query" and substitute it in place of the
original `query` field, leaving `expected`/labels untouched. This is the RL
proposal's cheapest falsifiable slice: a full bandit/policy loop is NOT built here
— only whether query rewriting moves the needle at all, which gates whether a
policy loop is worth building.

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

Lumen currently uses Minerva's verification pipeline (minerva_verify) with cross-model agreement and SpanUQ-lite voting, but search queries for fact-checking are not optimized via reinforcement learning.

## Proposal — proposed change

Develop a reinforcement learning loop that adjusts search query formulations based on Minerva's verification accuracy as a reward signal. This would involve creating a new script (e.g., /home/lumen/bin/rl_search_optimizer.py) that periodically analyzes past verification outcomes, proposes query variations, and updates a policy model (e.g., a simple bandit or contextual bandit) to maximize verification success. Integrate with existing Minerva services via a new systemd timer.

---

The report at `/home/lumen/foreman/pilots/64f310d0ed5d/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
