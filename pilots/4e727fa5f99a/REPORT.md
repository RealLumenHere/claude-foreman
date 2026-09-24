# PILOT 4e727fa5f99a report — MamaBench counterfactual suite for local model evaluation

Runner: orch-burn-A (wq#2126), 2026-09-24. No arm was scored: the fixed sample cannot be pinned
(dataset is gated) and the full run does not fit the budget. Nothing was built or wired.

## What was measured

| check | result |
|---|---|
| dataset | HelpMum-Personal/MamaBench, cc-by-4.0, 434 English narratives in 217 pairs, 371 pathologies, parquet |
| access | `gated: auto` in the HF API; datasets-server returns HTTP 401 without a token; no HF token on Lumen (`~/.cache/huggingface/token` absent, no HF_TOKEN in agent/.env) |
| what pelican_bench scores | 4 fixed probes, mechanical scorers, no judge. Rows on file: qwen36-local 0.955 (28), isis-qwen3-32b 0.963 (32), nephthys-qwen3-14b 0.875 (8) |
| how the paper scores | Bias Trap Rate with an LLM judge, which the paper itself lists as a bias source |
| hathor throughput, LiteLLM group `hathor-llama8b`, 150-token vignette, 120-token cap | 35.2 s on the first request, 15.4 s on the second (150 prompt / 120 completion tokens both times) |

## Findings

1. Baseline does not reproduce: the sample cannot be pinned. Getting the parquet needs a Hugging Face
   account that accepts the dataset's contact-information gate. That is a consent action for Frank,
   not something a pilot does for him.
2. Cost: 434 items at the warm 15.4 s is 6,680 s (111 min) of generation on one hathor slot, over the
   90 min budget before any judging. A 60-pair sample is 120 items, about 31 min of generation plus a
   judge pass of similar size on the same box. These are projections from two timed requests, not a run.
3. Relevance: the set covers maternal and paediatric diagnosis. Lumen's workloads are infrastructure
   and agent tasks, so a score on it says little about any model choice the fleet makes.
4. The transferable part is the metric, not the data: conditional failure on the counterfactual given
   success on the base case (BTR). That can be computed on any paired tasks Lumen already owns, without
   this dataset.

## If someone still wants it

Create an HF token, accept the gate once, put the token where the broker can reach it, and pin the 60
pair sample. Then run pelican_bench and a BTR runner against qwen36-local and isis-qwen3-32b. The
hypothesis needs a >= 10 point BTR gap to count as a win.

RECOMMENDATION: SKIP
