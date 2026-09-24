# PILOT 6161e33a06f9 report — encode redundant context as images in the prompt pipeline

Runner: orch-burn-B-3f4634 (wq#2422), 2026-09-24. Primary metric measured once; it fails in
the wrong direction, so no fidelity arm was run. Nothing was wired into any service.

## What was measured

Same content, both arms: the 2 static string literals in `persona_engine.py` (2,593 chars).

| arm | prompt tokens | cost per call at the route's input price |
|---|---|---|
| text, tiktoken cl100k (proxy tokenizer) | 615 | $0.0000185 on GLM-5.3-Flash cached ($0.03/M), $0.0000923 uncached ($0.15/M) |
| image, 1400 x 680 PNG via `lumen-vision` (Qwen3-VL-32B), gateway `prompt_tokens` | 941 (includes about 10 tokens of instruction) | $0.0000979 at $0.104/M |

- Token change: +53% (941 vs 615). The card's win condition was -30%.
- Price: 5.3x the cached text cost, 1.06x the uncached text cost.
- Absolute ceiling: even a perfect 100% token cut on this prompt saves $0.0000185 per call at the cached price.

## Findings

1. The hypothesis fails on its own metric. The image costs more tokens than the text it replaces.
2. The prompt is small. 615 tokens is the whole static part of the persona engine, and a cached text
   prefix costs about 1.9 cents per thousand calls (615 x $0.03/M x 1000).
3. `lumen-vision` is the only vision rung and it is a paid 32B cloud model. Using it would mean sending a
   cheap-model prompt to a pricier model to save tokens that cost almost nothing.

## Limits of this measurement

- One image, one renderer (PIL default bitmap font). A denser font would cut image tokens and hurt
  legibility, and legibility was not tested.
- 615 is a cl100k count. GLM and Qwen tokenizers will differ by a modest amount, not by the 1.5x needed to flip the sign.
- Only the persona prompt was measured, not `prompt_engine.py`'s assembled prompts.

RECOMMENDATION: SKIP — image arm 941 tokens vs 615 text (+53%, needs -30%); 5.3x the cost of cached text; 1 call, 1 image, cl100k proxy for the text count.
