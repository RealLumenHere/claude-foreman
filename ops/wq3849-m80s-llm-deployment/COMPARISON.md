# wq#3849 — M80s vs T5810 (Lumen), model-by-model

## T5810 (Lumen) baseline — measured, cited

From `research/LOCAL_MODEL_STRATEGY.md` (`research/tools/membw_probe.py`, 2026-09-11)
and `ops/wq3558-ollama-sched-wedge/README.md`:

- CPU: Xeon E5-2699 v4, 22C/44T, AVX2+FMA, no AVX-512.
- Memory: 4-channel DDR4-2400, raw probe 76.8 GB/s; the doc applies a measured ~5x
  worker-session undercount correction and lands on **~50 GB/s effective** for
  planning (`research/LOCAL_MODEL_STRATEGY.md:51`).
- Live Ollama config on Lumen today: `OLLAMA_MAX_LOADED_MODELS=1`,
  `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=30s` (`ops/wq3558-ollama-sched-wedge/README.md:4-5`).
- Qwen3.6-35B-A3B Q4_K_M on Lumen: **18–25 tok/s generation (estimate, not yet
  re-measured post wq#3558 fix)** (`research/LOCAL_MODEL_STRATEGY.md:131`).

## M80s — planning estimates only (no reachable node to measure yet)

From `research/WQ3798_M80S_LLM_MODEL_SELECTION.md` and
`scratch/research/m80s-llm-recommendations.md`:

- CPU: i7-12700, 8P+4E/20T, AVX2 + AVX-VNNI (int8 dot-product in hardware).
- Memory: 2-channel DDR4-3200, ~40–45 GB/s practical — **lower than T5810's 4-channel
  bandwidth**, so decode (bandwidth-bound) on a given model size is not expected to
  beat T5810; it may land somewhat behind it.
- AVX-VNNI does not move more bytes/sec, so it does not change the decode ceiling.
  What it changes is prefill (compute-bound): AVX2/AVX-VNNI int8 GEMM is reported
  30–500% faster than scalar/AVX1 llama.cpp prompt-eval on comparable weights
  (`research/WQ3798_M80S_LLM_MODEL_SELECTION.md:19`, citing justine.lol/matmul) — T5810
  already has AVX2 too (Broadwell-EP), so this is the lever that matters for M80s'
  comparison on *prompt-heavy* workloads, not decode.

| Model | Role | T5810 (measured/estimate) | M80s (planning estimate) |
|---|---|---|---|
| qwen3.6:35b-a3b | agent-fast | 18–25 tok/s gen (est., `LOCAL_MODEL_STRATEGY.md:131`) | 15–25 tok/s gen (est., `m80s-llm-recommendations.md:39`) |
| qwen2.5-coder:32b | agent-deep | not previously benchmarked on Lumen | 4–6 tok/s gen (est., `m80s-llm-recommendations.md:21`) |
| qwen3:32b | research | not previously benchmarked on Lumen | 4–6 tok/s gen (est., `m80s-llm-recommendations.md:30`) |
| qwen2.5-coder:14b | quick | not previously benchmarked on Lumen | 8–12 tok/s gen (est., `m80s-llm-recommendations.md:24`) |

Net expectation per `research/WQ3798_M80S_LLM_MODEL_SELECTION.md`: decode tok/s on
M80s should land in roughly the same range as the bandwidth-over-weight-size
arithmetic predicts for T5810 at a given model size (similar effective bandwidth);
M80s' edge is prefill on long prompts, which is exactly what the 16K/32K benchmark
below is designed to isolate.

## Results (to fill in — `benchmark.py` has not run; no node exists to run it on)

| Model | num_ctx | prefill tok/s (M80s) | decode tok/s (M80s) | prefill tok/s (T5810) | decode tok/s (T5810) |
|---|---|---|---|---|---|
| qwen3.6:35b-a3b | 16384 | — | — | — | — |
| qwen3.6:35b-a3b | 32768 | — | — | — | — |
| qwen2.5-coder:32b | 16384 | — | — | — | — |
| qwen2.5-coder:32b | 32768 | — | — | — | — |
| qwen3:32b | 16384 | — | — | — | — |
| qwen3:32b | 32768 | — | — | — | — |
| qwen2.5-coder:14b | 16384 | — | — | — | — |
| qwen2.5-coder:14b | 32768 | — | — | — | — |

Fill the M80s columns from `benchmark.py`'s `benchmark-results.json` once the node is
reachable and `pull-models.sh` has run. Fill the T5810 columns the same way, running
`benchmark.py` with `OLLAMA_HOST=http://127.0.0.1:11434` on Lumen itself for the two
models that fit T5810's current RAM budget (`qwen3.6:35b-a3b`, `qwen2.5-coder:14b` —
the 32B models were sized for M80s' 64GB target, not Lumen's current allocation; check
free RAM before loading them).
