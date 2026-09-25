# PILOT b4356831e1c7 — LoRA fine-tuning for local models to reduce gender bias
See /home/lumen/reports/pilots_1934_1983_2077_2125.md for full reasoning (wq#2125).
Baseline does not reproduce: PEFT/torch not installed anywhere on the fleet, and
lumen.gpu_policy forbids GPU compute use even where a GPU exists; no CPU-only path to
LoRA fine-tuning a 2.6B model in this budget. Fine-tuning-as-proposed needs an
accelerator the fleet does not have (same gap as a067b397408c).
RECOMMENDATION: SKIP
