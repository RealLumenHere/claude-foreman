# PILOT e19dc452745a report — prototype an attention-only transformer layer for local models

Runner: orch-burn-B-3f4634 (wq#2421), 2026-09-24. No arm was scored: the card's
precondition (the existing harness can score a bare layer) is false, so nothing was built.

## What was measured

| check | result |
|---|---|
| what `pelican_bench` scores | 4 fixed probes (`pelican_svg`, `json_extract`, `code_fib`, `exact_word`); each sends a prompt to LiteLLM `127.0.0.1:4000` and scores the reply text |
| baseline rows on file | 8 runs, 68 results: qwen36-local 0.955 (28), isis-qwen3-32b 0.963 (32), nephthys-qwen3-14b 0.875 (8) |
| scorer floor with no usable text | empty output: 0.0 / 0.0 / 0.25 / 0.0 (mean 0.0625); random text: 0.0 on all four (mean 0.0) |
| hathor toolchain | python 3.14.4, no numpy, no torch, no pip3 on PATH, CPU AVX only (no AVX2, no AVX-512), 24 threads |

## Findings

1. The harness measures generation quality of a served model. A single attention layer has no
   tokenizer, embedding table, LM head or trained weights, so it cannot be put behind the
   endpoint the probes call. Its score is the floor above, 0.0 to 0.0625, whatever the layer does.
2. Making the comparison real means training or adapting a full language model around the layer.
   That is not a 90 min, 150k-token pilot on hathor, which cannot even import numpy today.
3. The proposal says the current backbones are standard transformers and that no attention-only
   variant is in use. That is accurate, but the card gives no reason a local model needs one.
   The paper is theoretical, as the deferral said, and this pilot did not change that.

## If someone still wants it

A pilot that fits: take an existing open attention-only or hybrid checkpoint that already ships
GGUF weights, serve it through ollama on hathor, and run `pelican_bench --models <it>` against the
0.955 baseline. That needs a named checkpoint first; none is in the proposal.

RECOMMENDATION: SKIP — harness scores served LMs and a bare layer floors at 0.0-0.0625 vs 0.955 for qwen36-local; no checkpoint named to serve; 0 arms scored.
