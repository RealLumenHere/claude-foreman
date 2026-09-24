# PILOT aac675199d1e REPORT: AgentMemBench as a memory eval harness

## Result
Both facts fail the win condition.

1. **Runnable repo: not identifiable.** The paper is real (arXiv 2608.00009, 491 scored question turns from LoCoMo 200,
   MultiDoc2Dial 187, MSC 104; 5 strategies; generator and judge are Qwen2.5-7B 4-bit on a T4/P100). It says code, Docker image
   and result files are in "a public repository linked from this submission" but the text I fetched carries no URL, so step 1 of the
   proposal cannot be confirmed from here. The datasets are public on HuggingFace (Percena/locomo-mc10, IBM/multidoc2dial,
   gonced8/multi-session_chat).
2. **The harness gap is closed.** Lumen has one: golden v4 (`eval/golden_v4_frozen_20260814.jsonl`, sha256 8ba5afb0...),
   `recall_bench.py` with per-class R@1/5/10/MRR, trap@1 and a control gate, and `lumen-recall-bench.timer` running nightly with a
   drift alarm. Current `eval/sched_baseline.json` headline, n=239: R@1 0.778, R@5 0.912, R@10 0.933, MRR 0.836.
   The proposal's premise ("no experiment/eval harness") is stale.

## What it would add, and why that is small
Its headline result is that dense retrieval is the only strategy that survives long-horizon recall (LoCoMo Recall@5: dense 0.573,
every other family <= 0.005). Lumen already retrieves densely (pgvector, nomic embeddings), so the finding confirms the design rather than
changing it. The datasets are chat logs; Lumen's memory is fleet notes and facts, so scores would not transfer.

## Cost of running it anyway
Its Faithfulness and F1 need a generator and judge; the paper's numbers come from a 7B 4-bit model on a GPU. Lumen's only local
inference is CPU. A retrieval-only run (Recall@k, MRR) against Lumen's search would be cheap, but it needs a repo or a re-implementation
of the LoCoMo provenance scoring first.

RECOMMENDATION: SKIP
Reopen only if the repo URL turns up and we want LoCoMo as an external control for a wq#574 retrieval arm.
