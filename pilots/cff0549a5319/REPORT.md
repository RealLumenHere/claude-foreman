# PILOT cff0549a5319 — DAG-based reasoning consensus (wq#1901)

Run: 2026-09-26, on hathor via LiteLLM gateway model `hathor-llama8b`.
Code: `pilots/cff0549a5319/run_pilot.py` (treatment/DAG consensus at
`pilots/cff0549a5319/run_pilot.py:60-64`), fixed sample at
`pilots/cff0549a5319/fixture.json`, raw output at `pilots/cff0549a5319/results.json`.

## Hypothesis

Weighted-DAG multi-trace consensus (3 traces/item, agreement-weighted vote) improves
exact-match accuracy over a single-call baseline on the same fixed sample by a
detectable margin.

## Fixed sample

8 short-answer arithmetic/logic prompts with exact-match ground truth, pinned in
`fixture.json` (committed to this branch). Same 8 prompts, same model, both arms.

## Baseline (measured first)

1 call/item, temperature 0.7. Accuracy: **6/8 = 0.75**. Wall clock: 27.6s.
Misses: q2 (unit-conversion arithmetic), q3 (riddle).

## Treatment

3 calls/item, temperature 0.9, aggregated with the weighted-DAG consensus in
`run_pilot.py:60-64` (nodes = traces, edge weight = pairwise answer agreement, consensus
= highest-weight node). Accuracy: **5/8 = 0.625**. Wall clock: 16.7s (parallel-free,
timing noise from the gateway, not from the method).

## Result

Treatment did not beat baseline on this sample — it was one item worse (q7: three traces
split 9.6/9.60/9, and normalization collapsed the vote to a wrong plurality instead of
correcting it). The two baseline misses (q2, q3) were also reproduced identically across
all 3 treatment traces each time — the underlying model was confidently, consistently
wrong, so no amount of majority-voting over its own samples could fix it. Multi-trace
consensus over one small model's own samples cannot correct answers the model gets wrong
with high confidence every time; it can only clean up its low-confidence noise, and this
8B model didn't produce much of that on this sample.

n=8 is far too small to call this decisive, and there is still no eval harness (the gap
this pilot was deferred for) to run a larger, statistically meaningful sample or to
separate "wrong reasoning" from "wrong formatting." But the one falsifiable prediction
this pilot could make — treatment beats baseline on the fixed sample — is falsified by
the measurement in hand, not by opinion.

## Kill criteria check

- Wall-clock: 44.3s total, well under the 90 min budget. Not triggered.
- Baseline reproduced cleanly (accuracy computed from the fixture, no errors). Not
  triggered.
- Treatment completed at budget, not partial. Not triggered.

None of the early-stop kill criteria fired; this is a completed run, not a truncated one.

RECOMMENDATION: SKIP — measured on hathor-llama8b, n=8 fixed sample: baseline accuracy
0.75 (6/8), treatment (3-trace weighted-DAG consensus) accuracy 0.625 (5/8); treatment
wall clock 16.7s vs baseline 27.6s for 3x the calls. Treatment underperformed baseline
and both misses were traces agreeing confidently on the same wrong answer, which
consensus voting cannot fix. Revisit only after the eval-harness gap (cited in the
defer) exists and a larger sample can rule out this being sample noise.
