# PILOT 210e6d0a4dc8 report — ForgetBench for long-term parametric memory monitoring

Runner: orch-burn-2-4611f5 (wq#2078), 2026-09-24. Nothing was run or built: the card's two
preconditions fail. Nothing was wired into prod.

## What ForgetBench is (arXiv 2607.26455, 2026-07-29)

A benchmark of forgetting under continual knowledge editing: a model's weights are modified
by sequential editing methods, and the benchmark tracks how earlier facts decay across the
editing stages (concept QA and scenario QA). Its own limitations section says it covers
sequential knowledge editing only. The authors state the code follows acceptance.

## What was checked

| check | result |
|---|---|
| released code or dataset | none found on 2026-09-24 (paper, alphaxiv, takara.ai and a code search; only the abstract page and PDF) |
| anything in Lumen that edits or fine-tunes local model weights | none: no unit file matching lora / finetune / train / model-edit; local models are served frozen through llama-server and ollama behind LiteLLM |
| what the proposal would measure | a frozen model queried on a timer, which cannot forget between runs, so the trend table would be flat by construction (run-to-run noise only) |

## Findings

1. The mechanism the benchmark measures (forgetting caused by weight edits) does not exist in
   Lumen. Lumen's memory is external: Postgres and pgvector, `minerva.db`, consolidation by
   lumen-dream and lumen-reflector. A retention problem there would be a retrieval or
   consolidation defect, and ForgetBench does not test those.
2. The data is not obtainable yet, so even a proxy would be built from the paper's prose.
3. The deferral reason (academic, may not map to Lumen's architecture) is correct. It does
   not map.

## If someone wants long-term memory monitoring

Use what tests Lumen's actual memory: the golden recall set and nightly bench from wq#572 /
wq#595 (`eval/recall_golden.jsonl`, `lumen-recall-bench.timer`), which already tracks
retrieval accuracy, stale-served-over-current (trap@1) and abstention over time.

RECOMMENDATION: SKIP — benchmark measures weight-edit forgetting that Lumen has none of, and no code or data is released; 0 measurable arms.
