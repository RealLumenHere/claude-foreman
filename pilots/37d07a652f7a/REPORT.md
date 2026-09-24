# PILOT 37d07a652f7a REPORT: CubicQuant for LiteLLM quantization

## Result: all three preconditions fail
1. **LiteLLM holds no weights.** It is a routing proxy at localhost:4000. A "quantization step in the gateway" has nothing to quantize.
   Cloud rungs (Cerebras, DeepSeek, SambaNova, OpenRouter) are not ours to quantize; local rungs run in llama.cpp on their own nodes.
2. **Released code: none found.** The paper (arXiv 2608.06763, "CubicQuant: Parametric Non-Uniform Codebooks for High-Throughput LLM
   Inference with 1-8-Bit Weights") describes a packed-bit format whose codebook is regenerated from a cubic and compact metadata
   inside the consuming GPU tile, built for GPU matrix kernels. My searches returned the paper only, no repo or kernels.
3. **Target hardware.** The format's value is on GPU dot-product paths. Lumen's local inference is CPU-only: T5810 (Lumen),
   Mac Pro 6,1 (hathor, isis) whose D300 cards are not inference devices in the fleet state, and hathor decodes an 8B at 11 to 16 tok/s on CPU (lumen_state, 2026-09-20 bench).
   Cloud GPUs are not ours.

## Note
For CPU, the relevant work is in llama.cpp's own low-bit formats and kernels, which Lumen already gets by updating llama.cpp.

RECOMMENDATION: SKIP
