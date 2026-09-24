# PILOT 6914f9109bf1 report: self-speculative decoding for local reasoning models

Runner: orch-burn-C (wq#2447), 2026-09-24. Nothing wired into prod, nothing installed, no downloads, production ollama and ufw untouched. Spec, sample and verdict rule were written into CARD.md before the first scored call (blank original in data/CARD.blank.orig).

## What was tested
llama.cpp draft-model-free (n-gram) self-speculative decoding, `--spec-type` ngram-simple / ngram-map-k / ngram-mod at DEFAULT parameters, on qwen3:14b Q4_K_M, CPU only, nephthys (E5-2697 v2, AVX only, no AVX2/AVX-512/AMX). Standalone ollama-bundled llama-server (build 96278e39f) on 127.0.0.1:18447, 12 threads, ctx 4096, one slot, one request at a time, greedy, seed 1, 160 new tokens, cache off. 8 fixed prompts (data/prompts.jsonl, sha256 bd3168a4a35a8194263804030caf25185951ffbbac33bfa6e95366f348ce50ea): R1-R4 reasoning, C1-C4 copy-heavy edits. Draft-MODEL speculation was out of scope (no draft model resident, nothing may be pulled).
Node deviation: the card said hathor; hathor was occupied by fleet worker wq#2495 (load ~12), so nephthys (idle test-bench node) was used.

## Results (data/analyze.out, data/results.json)
Baseline decode: 5.05-5.13 tok/s on every prompt. Drift check (second baseline / first): 1.001. No ollama client touched the node during any counted run (data/conn.log: 0 contaminated samples across 334).

| config | reasoning median ratio | copy median ratio | prefill ratio | outputs identical | draft tokens proposed / accepted |
|---|---|---|---|---|---|
| ngram-simple | 0.931 | 1.003 | 1.002 | 7/8 (R2 diverges at char 303) | 125 / 11% |
| ngram-map-k | 0.993 | 0.997 | 0.999 | 8/8 | 35 / 2.9% |
| ngram-mod | 0.994 | 1.003 | 1.000 | 8/8 | 0 |

Ratio = decode tok/s with speculation / baseline, server-reported. Best reasoning ratio 0.994 against a 1.25 bar. Best copy ratio 1.003.

## Against the pre-registered rule
ADOPT needs reasoning median >= 1.25, >= 6/8 identical, prefill >= 0.95, drift within 10%. SKIP if best reasoning median < 1.05 AND best copy median < 1.15. Both SKIP conditions hold (0.994 and 1.003). Drift 1.001 means the measurement noise is about 0.1%, so the flat ratios are real, not lost in noise.

## Why it does nothing here (observation, not tuned)
Outputs are only 160 tokens and prompts are 36-155 tokens, so there is little repeated text for an n-gram lookup to hit. ngram-mod's default match length (24) never fired, ngram-map-k proposed 35 tokens in 8 requests, ngram-simple proposed 125 and lost about 7% on reasoning prompts where its guesses were rejected. Copy-heavy prompts did not help either. Tuning the n-gram sizes or running long-output/RAG-style prompts was not done, because the card fixed defaults before the run.

## Integration note
No new proxy would be needed. LiteLLM already reaches llama.cpp servers through openai/ base URLs (qwen36-local and qwen36-live point at isis :8095, started by llama-qwen36.service). Turning this on would be a launch-flag change on that unit. That model is a MoE (qwen3.6-35b-a3b); this pilot measured a dense 14B, so nothing here transfers directly to it.

## Incident during the run (disclosure)
The first run was cut short: nephthys lost power abruptly at 18:33:00Z with 7 of 8 baseline prompts done (data/nephthys_crash_run1/). Two things ran at once: this benchmark (12 threads) and a 4k-token prefill on production ollama from a fleet worker dispatched to nephthys-qwen14b (the worker was spawned by a wq ticket I filed at 18:32:56; I stopped it and re-laned the ticket). It auto-rebooted at 18:33:27, died again at 18:33:36 and stayed off until I AC-cycled plug p2 at 18:40. The rerun used a guard so a config only starts with no ollama client, and aborts if one appears (data/run_v2.sh, data/run_v3.sh). v2 ran the baseline; v3 ran the remaining configs and the drift baseline, with a 120 s cooldown between configs instead of waiting for load < 1 (v2's guard counted its own load). Baseline was measured first, from a cold node, as the card requires.

## Caveats
8 prompts, 160 tokens each, one dense model, defaults only, one node. Reasoning prompts were cut at 160 tokens so most of the thinking trace is unseen. A serious follow-up would use long outputs and the real serving model.

RECOMMENDATION: SKIP - best reasoning-prompt decode ratio 0.994 (bar 1.25), best copy-prompt ratio 1.003, drift 1.001, prefill unchanged; ngram-simple lost 7% on reasoning, ngram-mod never drafted.
