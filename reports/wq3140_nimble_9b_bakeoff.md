# wq#3140 — Bespoke-Nimble-9B as a local classification rung: REJECT

Proposal df5d8e824b93, factory plan 7f41ebb3c569 (yellow, config_edit). Verdict:
reject. The bake-off could not run as proposed. Nothing was applied. The LiteLLM
config and the classifier are untouched.

## Evidence (2026-09-29)

1. **The model is not an Ollama model.** HF API `models/bespokelabs/Bespoke-Nimble-9B`:
   `library_name=peft`, `base_model:adapter:Qwen/Qwen3.5-9B`. The files are
   `adapter_model.safetensors` (~165 MiB) plus the tokenizer and prompt code. The repo
   has no GGUF, and an HF search for `Bespoke-Nimble` finds no quantized derivative,
   only `-v2` (also a PEFT adapter). The plan's route
   `ollama/bespokelabs/Bespoke-Nimble-9B` names a model that `ollama pull` cannot fetch.
2. **It does not generate text.** The model card says it "scores the allowed answer
   tokens directly; the evaluated workflow does not generate reasoning or free-form
   answers". `inference.py` `candidate_logits()` gathers last-position logits over
   candidate token ids and softmaxes them. A LiteLLM chat-completions rung would
   bypass that contract, so a bake-off through it would measure a different model.
3. **No host in the fleet can run it on a supported path.** The upstream quickstart
   (github.com/bespokelabsai/nimble) supports only an NVIDIA GPU with BF16 or
   Apple-Silicon MLX with unquantized weights (about 18 GB of weights alone).
   `inference.py:105` raises without CUDA-BF16. lumen has no GPU (Xeon E5-2699 v4,
   RX550 display only), and isis is dead per ORCHESTRATOR-UPPER-HAND.
4. **The acceptance gate can't be met.** The proposal promotes the model only if it
   "beats the current classifier rung on agreement and latency". The incumbent rung is
   `cerebras-oss-120b` (`/home/lumen/minerva/minerva_classifier.py:25`). An
   unquantized 9B forward pass per field on CPU cannot beat Cerebras on latency.
5. **The plan's draft diff is malformed.** It appends a JSON object into
   `litellm_config.yaml`'s YAML `model_list`, and its `apply_cmd` only takes a backup.

## Re-proposal conditions

Re-propose only when a CUDA-BF16 or ≥32 GB Apple-Silicon host has spare capacity.
Run the model there as a standalone scorer service using upstream's merged-weights
runner, with a candidate-logit API rather than a LiteLLM chat rung. Score it on the
~200 hand-labelled minerva.db items against `cerebras-oss-120b`.

## Pending (not worker-runnable)

`minerva-factory` is not on the build-worker Bash allowlist (ISSUE-LOG #240), so the
Orchestrator must run:

    ssh lumen@<lumen> '/home/lumen/bin/minerva-factory close df5d8e824b93 --reject "wq#3140: PEFT LoRA logit-scorer, no GGUF/Ollama path, needs CUDA-BF16 or MLX; no fleet host; see foreman reports/wq3140_nimble_9b_bakeoff.md"'
