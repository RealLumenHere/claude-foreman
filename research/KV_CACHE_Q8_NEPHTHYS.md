# q8_0 KV cache + flash attention on nephthys (wq#2499)

Status: **arm A measured, arm B not run.** No build worker can get a shell on
nephthys (ISSUE-LOG row 696). The q8_0 arm is staged and ready to run at
`ops/wq2499-kvcache-ab/`.

## Setup (verified 2026-09-25)

- nephthys, ollama 0.32.6, `qwen3:14b` Q4_K_M (14.8B), CPU-only (`size_vram: 0`).
- The orch re-verify note on the ticket reads override.conf as: `OLLAMA_CONTEXT_LENGTH=32768`,
  `NUM_PARALLEL=2`, `MAX_LOADED_MODELS=1`, `KEEP_ALIVE=2h`, no FLASH_ATTENTION, no
  KV_CACHE_TYPE. `/api/ps` agrees: `"context_length":32768`. So DISPATCH_DISSECTION F2's
  "4096 served ctx" no longer holds for nephthys. It was raised on 2026-09-20.
- Architecture (`/api/show`): 40 blocks, 8 KV heads, key/value length 128.

## KV arithmetic

Per token, f16 KV = 40 layers × 8 heads × 128 × 2 (K+V) × 2 B = **160 KiB**.
q8_0 is 34 B per 32 elements (1.0625 B/elt), which gives **85 KiB**.

| KV dtype | 32768 × 2 slots (65536 tok) | 40960 × 2 slots | 32768 × 4 slots |
|---|---|---|---|
| f16  | 10.00 GiB | 12.50 GiB | 20.00 GiB |
| q8_0 |  5.31 GiB |  6.64 GiB | 10.63 GiB |

The arithmetic matches the measurement. Loaded size under f16 is 19.31 GiB
(`/api/ps`), which is about 9.3 GiB of weights plus the 10.0 GiB of KV.

## Measurements

Method: `ops/wq2499-kvcache-ab/kvbench.py`, 3 reps. Each rep uses a fixed ~520-token
prompt with a unique prefix, so the prompt cache never hits. `num_predict=64`,
temperature 0, thinking off. Throughput comes from ollama's own
`prompt_eval_duration`/`eval_duration`. The reported value is the mean of reps 1-2,
because rep 0 pays for the model load.

| arm | prefill tok/s | decode tok/s | loaded size | runner RSS |
|---|---|---|---|---|
| A: f16 KV, no FA (current) | 8.09 (8.04/8.04/8.14) | 4.79 (4.82/4.80/4.79) | 19.31 GiB | not taken (no shell) |
| B: q8_0 KV + FA | not run | not run | predicted ~14.6 GiB | not run |

Arm A was measured 2026-09-25T03:40Z. Each rep prefilled 529 tokens and generated 64.
The reps were tight (spread under 1.3%), so a ±5% delta in arm B will be clear
of the noise. The 8.09 prefill figure revises inference_nodes.yaml's 7.4. That older
number came from a longer fworker prompt, where prefill slows as the context
fills. Decode had never been measured before (`decode_tps: null`).

## Recommendation

**Not adopted.** Arm B has not been measured, so there is no decode delta to
recommend on. This ticket says the decode cost has to be measured, and one bench
(sotaaz, A100) shows q8_0 KV slowing decode. On a CPU-only box, flash attention may
also change prefill in either direction.

If arm B holds decode within ~5% of arm A, adopt it. The ~4.7 GiB it frees at the
current 32k × 2 buys one of these:
- `NUM_PARALLEL` 2→4 at 32768 ctx. KV would be 10.6 GiB, about the same as today's f16
  footprint. This doubles local-worker slots for the same RAM.
- ctx 40960, the model's trained max and the `models.yaml` declared window, at 2 slots.
  KV would be 6.6 GiB.
- For F2 on hathor (still pending): the same flags would cut llama3.1:8b's 32k KV
  (32 layers × 8 × 128, 128 KiB/token f16) from 8 GiB to 4.25 GiB at 2 slots.

If decode drops by more than ~10%, reject it. Prefill is the measured bottleneck, and
at 32k × 2 slots on a 64 GB box RAM is not tight (19.3 GiB loaded).
