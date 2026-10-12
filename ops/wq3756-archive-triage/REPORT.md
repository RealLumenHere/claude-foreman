# wq#3756 archive triage — top proposals

Pipeline: 1881 total -> 1881 within 6 months -> 1684 past spam filter -> 518 hardware/category hits -> top 30 never-locally-tested

Finding: zero proposals in the archive mention RK3399, MT8167, Ingenic T40, PiKVM, MaskROM, or Rockchip at all (verified by direct substring search over all 1881 rows, not just the filtered set) — the self-improvement proposal pipeline has never produced Ingenic/MediaTek firmware, PiKVM-alternative, or Rockchip MaskROM proposals. The real hardware-relevant buried value is concentrated in Mac Pro 6,1 (nephthys) / BC-250 (hapi) local-LLM-optimization and never-benchmarked-eval proposals below.

| score | id | status | hardware | categories | title |
|---|---|---|---|---|---|
| 7 | cb9f26f4f695 | reject | mac_pro_6,1 | local_llm_optimization,model_eval_never_benchmarked | Benchmark speculative decoding on local Ollama rungs before any gateway integration |
| 7 | 64de9f9a18ff | reject | mac_pro_6,1 | local_llm_optimization,model_eval_never_benchmarked | Reject DR-to-Long long-context data synthesis (no fine-tuning substrate on Lumen) |
| 7 | 7ba4445cf9f5 | rejected_aged | mac_pro_6,1 | local_llm_optimization,model_eval_never_benchmarked | Defer KV-Cache Quantization Until Eval Harness Available |
| 6 | 27af9c619476 | staged | mac_pro_6,1 | local_llm_optimization | Add Qwen3.8-9B to LiteLLM gateway for local inference |
| 6 | 0b019c06c0c2 | reject | bc-250,mac_pro_6,1 | - | Defer QFedPolyp federated learning adoption |
| 5 | c3f7848e36ac | reject | mac_pro_6,1 | model_eval_never_benchmarked | Prototype source-supervised memory-replay reward adaptation for Minerva proposal scoring |
| 5 | 445dd691c7c5 | reject | mac_pro_6,1 | model_eval_never_benchmarked | Adopt chess APO benchmark as an offline prompt-optimization testbed |
| 5 | 925d1e17f7fc | reject | mac_pro_6,1 | local_llm_optimization | Evaluate ShamAN-Q sub-1-bit quantization for local rung weight compression |
| 5 | f412fee34e3a | reject | mac_pro_6,1 | model_eval_never_benchmarked | Add NAQD-Env selective-withdrawal scenarios as a Minerva eval harness |
| 5 | 7ca0c73ec7f8 | duplicate | mac_pro_6,1 | local_llm_optimization | Evaluate Swift-1.5-Qwen3.8-27b as a token-efficient reasoning rung via Ollama + LiteLLM |
| 5 | 785b55174d32 | reject | mac_pro_6,1 | model_eval_never_benchmarked | Add CheatBench reward-gaming probes to the Minerva eval gate |
| 5 | 874d32da8603 | reject | mac_pro_6,1 | local_llm_optimization | Adopt attention-output mixed-precision quantization for local Ollama rungs |
| 5 | 91ecc9681e22 | reject | mac_pro_6,1 | model_eval_never_benchmarked | Adopt ROTE as a symbolic-memorization probe for local rungs |
| 5 | 7cf007aa0305 | duplicate | mac_pro_6,1 | local_llm_optimization | Add Swift-1.5-Qwen3.8-27B GGUF as a LiteLLM research rung |
| 5 | c9eea98b9c03 | reject | bc-250 | local_llm_optimization | Reject NGN differentiable-boundary pruning for Lumen adapters |
| 5 | 87e10fc51592 | planned | mac_pro_6,1 | - | Patch netdata agents to CVE-2026-83602 patched version and verify IP allowlist enforcement |
| 5 | 4a518c4e703b | planned | bc-250 | - | Apply BC-250 40 CU unlock kernel patch to hapi blades |
| 5 | a960e843925a | approved | mac_pro_6,1 | - | Implement co-occurrence network topic model for research snippet clustering |
| 4 | 3123270aa5a2 | reject | - | local_llm_optimization,model_eval_never_benchmarked | Reject Lingtai concept-anchor telemetry for Minerva |
| 4 | 6a9871111d96 | reject | - | local_llm_optimization,model_eval_never_benchmarked | Add ITE repetition-bias probe to Minerva eval pipeline |
| 4 | 9c70268c4da3 | reject | - | local_llm_optimization,model_eval_never_benchmarked | Evaluate ATD as a research-only probe against existing local rungs |
| 4 | 2d0c3bcebf95 | reject | - | local_llm_optimization,model_eval_never_benchmarked | Reject CAT/UA search-aware loss adoption — no ATP pipeline and no fine-tuning loop exists |
| 4 | 6a0103b00e03 | reject | - | local_llm_optimization,model_eval_never_benchmarked | Adopt ZoAQ query-reuse for local black-box model probing |
| 4 | d653cc13c6a3 | planned | - | model_eval_never_benchmarked | Add competence-preserving perturbation stability check to Minerva eval |
| 4 | ed7b91227587 | planned | - | model_eval_never_benchmarked | Add ECE/Brier calibration probe to Minerva eval gate |
| 4 | c91bf1d70333 | staged | mac_pro_6,1 | - | Add Jais 2 to LiteLLM gateway for Arabic support |
| 4 | 3078fc82a3cc | planned | - | model_eval_never_benchmarked | Integrate REFORGE dataset into Lumen's model evaluation pipeline |
| 4 | ab32779deb55 | planned | - | model_eval_never_benchmarked | Implement ICO-style iterative context optimization as a jailbreak resistance test harness |
| 4 | 67395ad51cac | planned | - | model_eval_never_benchmarked | Benchmark Lumen's pgvector memory with MemArena tasks |
| 4 | 4f64e17e9cf2 | staged | mac_pro_6,1 | - | Add Qwen‑3.8 model to LiteLLM gateway and Minerva model pool |
