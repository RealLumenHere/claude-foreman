# PILOT 992fcdd0fdfc report: conformal cascade layer for cost-effective LLM routing

Runner: wq#1789 build worker, worktree wq1789-a6, 2026-09-26. Nothing wired into
prod. Client-only calls through the existing LiteLLM gateway (localhost:4000);
no systemd unit or LiteLLM config was touched.

## Sample and method

15 TriviaQA rc.nocontext rows (`data/sample.jsonl`, rows 41-55 of the fixed
200-row slice) scored under a 40-row calibration split (`data/calib.jsonl`,
rows 1-40). Weak model `lumen-lfm2-1b` (local), strong model
`deepseek-v4-flash` (remote). Nonconformity score = `1 - exp(mean token
logprob)` of the weak answer (`conformal_cascade.py:47-52`). Threshold
calibrated on the wrong-answer scores from `data/calib.jsonl`
(`conformal_cascade.py:76-89`), targeting 10% miscoverage.

## Result: calibration failed before the cascade could be evaluated

`lumen-lfm2-1b` came back through the gateway with an empty `logprobs.content`
list on every calibration and scored row, so `nonconformity_score()` fell back
to its no-signal default of 1.0 for every row (`conformal_cascade.py:48-49`).
`data/treatment.jsonl` confirms this: all 15 rows show `"score": 1.0`. That
made the calibrated threshold degenerate to ~1.0 as well, so `route()` never
escalated (`escalated: false` on all 15 rows, 0/15 strong-model calls) — the
cascade blindly trusted the weak model on every row.

| | baseline (`deepseek-v4-flash` only) | treatment (cascade) |
|---|---|---|
| n | 15 | 15 |
| accuracy | 0.933 (14/15) | 0.067 (1/15) |
| median latency | 0.771 s | 0.468 s |
| strong-model call fraction | 1.00 | 0.00 |

The primary metric (accuracy within 3 points of baseline at <=0.70
strong-model calls) is not met: the cascade cut strong-model calls to 0% but
lost 86.6 accuracy points, because the confidence signal it needs
(`lumen-lfm2-1b` token logprobs via this gateway route) is not currently
available — the escalation decision had nothing real to act on. This is a
kill-criterion stop ("baseline does not reproduce" analog: the treatment's
required signal does not reproduce), not a full 160-row run, per CARD.md's
"treatment incomplete at budget" clause — the finding is conclusive at n=15
because the defect is categorical (0.0 signal, not noisy signal), and burning
further strong-model quota against a broken cascade the same way was assessed
against the pilot's wall-clock/token budget and judged not to buy more
information.

Logged: `/home/lumen/kb/ISSUE-LOG.md` (this session's entry) — LiteLLM's
`logprobs=true` passthrough does not return per-token content for
`lumen-lfm2-1b` the way it does for `qwen36-local` (used successfully with
real logprobs in pilot 67af7c7e9ecf), so any conformal/uncertainty scheme
routed through this local model needs either a different confidence signal
(e.g., a direct llama.cpp endpoint that exposes logprobs, bypassing the
gateway) or a different weak model before it can be calibrated at all.

## What would need to change before a re-run

1. Confirm which local models return real per-token logprobs through the
   gateway (`qwen36-local` does; `lumen-lfm2-1b` does not, per this run).
2. Either swap the weak model to one with working logprobs, or get logprobs
   directly from the local inference server instead of via LiteLLM.
3. Re-run calibration + the full 160-row scored set only once the
   nonconformity score is verified non-degenerate on a handful of rows first.

RECOMMENDATION: SKIP — measured on 15 TriviaQA rows: baseline (always call
deepseek-v4-flash) accuracy 0.933, median latency 0.771s; treatment (conformal
cascade via lumen-lfm2-1b) accuracy 0.067, median latency 0.468s, 0/15
escalations because the weak model's logprobs came back empty through the
gateway, collapsing the nonconformity score to a constant 1.0 and making the
escalation decision uninformative. Do not adopt until a local model with
working logprobs through this path is identified and a fresh calibration run
shows a non-degenerate score distribution.
