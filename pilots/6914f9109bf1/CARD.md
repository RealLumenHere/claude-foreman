# PILOT 6914f9109bf1 — Implement Self-Speculative Decoding for Local Reasoning Models

Experiment card. Generated 2026-09-24T09:15:01+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `6914f9109bf1` |
| Deferred | 2026-08-24 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/6914f9109bf1/REPORT.md` |

## Why this is a pilot and not a research prompt

The technique is promising but requires careful evaluation of latency gains vs. overhead on current hardware (no AVX-512/AMX) and integration with LiteLLM; a specific benchmark is needed to confirm feasibility.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis
(Filled in by the runner, orch-burn-C / wq#2447, 2026-09-24 before any scored call, per this card's "edit it before the run" clause: the card was generated blank. Original: data/CARD.blank.orig.)

llama.cpp's draft-model-free (n-gram) self-speculative decoding, best of ngram-simple / ngram-map-k / ngram-mod at default parameters, raises single-stream greedy decode throughput of qwen3:14b Q4_K_M on a CPU-only Ivy Bridge Mac Pro (AVX only, no AVX2/AVX-512/AMX) by >= 1.25x (median over 4 reasoning prompts), while output stays identical to non-speculative decoding.

## Primary metric
Median over R1-R4 of (decode tok/s with speculation / decode tok/s baseline), best treatment config. decode tok/s = server-reported predicted_per_second. Win: >= 1.25.
Secondary (reported): same ratio over copy-heavy C1-C4; draft acceptance (draft_n_accepted / draft_n); prefill tok/s ratio (must be >= 0.95); losslessness = count of the 8 outputs byte-identical to baseline (need >= 6/8, first divergence reported for the rest); drift = second baseline run / first (must be within 0.90-1.10 or the verdict is WATCH whatever the ratios say).
Verdict rule, fixed now: ADOPT if reasoning-median >= 1.25 AND >= 6/8 identical AND prefill ratio >= 0.95 AND drift within +-10%. SKIP if best reasoning-median < 1.05 AND best copy-median < 1.15. Anything else WATCH.

## Fixed sample
data/prompts.jsonl, 8 prompts, sha256 bd3168a4a35a8194263804030caf25185951ffbbac33bfa6e95366f348ce50ea. R1-R4 are reasoning prompts (thinking trace, the target workload); C1-C4 copy-heavy edit prompts (JSON, function, paragraph, table) where n-gram lookup is expected to help most. Every arm: /v1/chat/completions, temperature 0, seed 1, max_tokens 160, cache_prompt false, one slot, one request at a time, 12 threads, ctx 4096.
NODE DEVIATION: the table says hathor. Hathor is occupied by fleet worker wq#2495 (model hathor-llama8b, load ~12, all hathor LiteLLM groups timing out at 100 s during wq#2446's probe), so any timing there is void. Run instead on nephthys (E5-2697 v2, 64 GB, load 0.00 at start, designated test-bench node since remediation_hold cleared 2026-09-18), as a standalone llama-server on 127.0.0.1:18447 using the ollama-bundled binary (build 96278e39f) and the resident qwen3:14b Q4_K_M blob. Production ollama and ufw untouched, nothing installed, no downloads.
Out of scope: draft-MODEL speculation (no draft model is resident and nothing may be pulled); the proposal's draft-model wording is therefore not tested.

## Baseline (measure FIRST)
No speculation (--spec-type unset), the first config the harness runs. Repeated at the end (none2) as the drift check.

```
ssh nephthys@192.168.8.176 'cd /home/nephthys/pilots/wq2447 && setsid nohup bash run.sh > run.log 2>&1 &'
```

## Treatment
Same harness, same prompts, three configs: --spec-type ngram-simple, ngram-map-k, ngram-mod, all at default parameters (no tuning).
Analysis: python3 /home/lumen/foreman/pilots/6914f9109bf1/data/analyze.py  (reads the res_*.jsonl copied back to data/).

Extra kill criterion: if the binary rejects a --spec-type value, drop that config and report it.

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

Lumen routes all inference through LiteLLM (localhost:4000) with local models like lumen-lfm2-thinking and qwen36-local, but no speculative decoding is currently used.

## Proposal — proposed change

Add a self-speculative decoding wrapper (e.g., using a draft model to propose reasoning tokens and a target model to verify) for local reasoning models, integrated as a LiteLLM custom callback or a separate proxy service.

---

The report at `/home/lumen/foreman/pilots/6914f9109bf1/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
