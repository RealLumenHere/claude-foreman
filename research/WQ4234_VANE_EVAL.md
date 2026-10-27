# wq#4234: EVALUATE Vane (formerly Perplexica), an AI answer engine in front of the search tier

**VERDICT: DO NOT ADOPT.** Vane would replace a working tier with a broken one. It
searches only through SearXNG, and the fleet's SearXNG engines are CAPTCHA-blocked
right now. The model it would synthesize with (qwen3.6 on isis) is down. Even when isis
is up, it needs about 1.5–2 min per answer and scored 0.19 on medium research questions.
The useful part of the idea ("give agents a digested, cited answer instead of raw
results") is about 30 lines on top of `research/search_tier.py` and the existing
gateway. That needs no new service, and `research/wq4234_vane_probe.py` already
demonstrates it.

Desk evaluation only, per the reopen note (stabilization freeze, no container runs, no
deploys). Live probes were limited to the fleet's own search tier and LiteLLM gateway.

## 1. What Vane is (upstream facts, 2026-10-27)

- `ItzCrazyKns/Vane` is Perplexica, rebranded in March 2026. Latest release is v1.12.2
  (2026-04-10). MIT license. Next.js app on port 3000. It was already flagged as "do not
  deploy" for the SearXNG rollout (`research/SEARXNG_FLEET_INTEGRATION.md:73,202`).
- Two images: the full image bundles a private SearXNG, and `slim-latest` points at an
  existing one via `SEARXNG_API_URL`.
- **The search backend is SearXNG only.** The ticket says "SearXNG, Brave, Tavily", but
  upstream's README says "Support for Tavily and Exa coming soon". The "Tavily, Brave, and
  Exa are supported" claim comes from a third-party aggregator page (repocloud.io), not
  from upstream.
- LLM providers: Ollama, OpenAI, Anthropic, Gemini, Groq, or any OpenAI-compatible
  server. These are configured on the first-visit setup screen, along with a chat model
  and an embedding model. Speed/balanced/quality modes; balanced and quality also rerank
  by embeddings.
- Programmatic API: stateless `POST /api/search` (body `chatModel`, `embeddingModel`,
  `optimizationMode`, `focusMode`, `query`, `history`, `stream`), which returns
  `{message, sources}`.

## 2. Resource requirements

| item | figure | source |
|---|---|---|
| install/update | builds from source, ~10 min on 2 cores, needs 4 GB RAM + 12 GB disk | upstream install notes (fleet-search, 2026-10-27) |
| idle | Vane + bundled SearXNG ≈ 500 MB RAM | same |
| per answer | ≥2 LLM calls (query rewrite + synthesis) plus embeddings in balanced/quality modes | Perplexica search agent design |
| GPU | none required, so it passes `research/fleet_hardware.json` `nvidia_cuda: false` | — |

The container is cheap. The cost is the LLM. On isis, qwen3.6-35b-a3b on llama-server
runs about 63 tok/s prefill and 10.9 tok/s decode
(`research/QWEN36_LOCAL_WORKER_SERVING.md:16`). A Vane synthesis prompt (system prompt
plus 6–15 source chunks) is about 3–5k tokens, so that's ~60–80 s of prefill, ~30 s to
decode a 300-token answer, and another LLM round-trip for the query rewriter. That comes
to **~1.5–2 min per answer**, before qwen3's hidden thinking tokens (wq#4101,
ISSUE-LOG 1373). Through Ollama it gets worse: Ollama loads at num_ctx 4096 and
silently truncates (wq#4212, ISSUE-LOG 1414), and Vane does not set num_ctx.
By comparison, `search_tier` gives each tier a 5 s budget (`research/search_tier.py`
`TIMEOUT_S = 5`).

## 3. Answer quality with qwen3.6

qwen3.6 could not be measured live today. isis is `state: down`
(`research/inference_nodes.yaml:119`), and `:11434` timed out at 5 s. The gateway key
lists `qwen36-local` but returns `400 Invalid model name`, and the same happens for
`nephthys-qwen14b` and `local-worker` (ISSUE-LOG row 1601).

Best available evidence:

- **Prior qwen3.6 research-answer bench** (`/home/lumen/logs/bench-qwen36-t1t2.json`,
  gen_model `isis-qwen36`, n=9): mean **0.378**, easy 0.61, **medium 0.19**. The weak
  dimensions were concision (0.1) and integration_specificity (0.2–0.3), which are the
  dimensions a "pre-digested answer" exists to provide.
- **Live Vane-shaped probe** (`research/wq4234_vane_probe.py`: tiered search, top 6
  snippets, then a cited ≤150-word synthesis through the gateway), with the question
  "What is the default context window Ollama loads a model with, and how do you raise it?":
  - search: 6 results in 0.3–1.8 s
  - `deepseek-v4-flash`: 606-char answer in **1.9 s**, every claim cited [n]. It flagged
    the conflict between sources (VRAM-tiered default vs. a flat 2048) and said where the
    sources were cut off. The pattern works with a model that is actually up.
  - `lumen/code`: 401, not on the key's allowlist. `qwen36-local` and
    `nephthys-qwen14b`: 400, as above.

## 4. Integration with `research/trigger.py`

The seam already exists. `trigger_one` takes `search=search_tier.findings_block`
(`research/trigger.py:326`) and puts its text into the brief as "Search findings"
(`trigger.py:332-333`). The brief then goes to the research-synthesis model, which is
itself an LLM synthesis step. Putting Vane at that seam would cause four problems:

1. **It would downgrade the search tier.** `search_tier` tries DeepSeek Search first,
   then stealth-browser, then SearXNG. Vane can only use SearXNG, which is the last
   fallback. Every fleet-search call this session reported "searxng general engines all
   suspended/blocked; brave fallback also failed" (DuckDuckGo/Startpage CAPTCHA, Google
   CSE/Brave rate-limited). A bundled SearXNG on the same egress IP would hit the same
   blocks.
2. **It would add a second synthesis step.** trigger.py already sends the brief to an LLM
   to draft. Summarizing first and then drafting from the summary throws away the source
   snippets the drafter could have cited, and it adds 1.5–2 min plus a failure point
   before `DAILY_DISPATCH_CAP` even comes into play.
3. **It would need a new always-on service**, which needs Gatus monitoring, an update
   path (a ~10 min source build on every update), and setup-screen state. That conflicts
   with the stabilization freeze.
4. **It would break fail-soft.** `findings_block` returns "" on failure. Vane would need
   its own timeout/fallback wrapper to keep trigger.py's §1 rule that it never blocks on
   a dead dependency.

## 5. Recommendation

- Do not deploy Vane, now or after the freeze, unless upstream ships a non-SearXNG
  backend (Tavily/Exa are still "coming soon").
- If agents do want digested answers, add an optional `synthesize(query)` next to
  `findings_block` in `search_tier.py`. It would run `tiered_search` and then make one
  gateway call with the probe's citation-only prompt, fail-soft to the raw block. That
  keeps the DeepSeek tier, has no new service, and costs ~2 s on deepseek-v4-flash. Gate
  it on isis/qwen3.6 only after isis returns and the wq#4101/wq#4212 thinking and num_ctx
  fixes are confirmed live.

## Re-run

```
python3 research/wq4234_vane_probe.py deepseek-v4-flash
python3 research/wq4234_vane_probe.py qwen36-local   # fails (none=True) until isis is served again
```
