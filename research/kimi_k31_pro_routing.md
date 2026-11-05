# Kimi K3.1 Pro: routing evaluation (wq#4852)

All figures fetched 2026-11-05. Prices are USD per 1M tokens (in / out).

## Verdict

Kimi K3.1 Pro is widely available, LiteLLM already maps it, and the license allows our use. It is **not cost-competitive with the fleet brain**: it costs about 7x more on input and 8x more on output than DeepSeek's `deepseek-flash` at peak rates. It also costs more than DeepSeek V4 Pro. I am **not adding it to `research/models.yaml`** and did not run the 3–5 ticket trial, because the ticket made both conditional on the model being cost-competitive. If we later want a frontier-class escalation rung, the route is a config-only change (see "If we do add it").

## 1. Availability and price

| Host | Available | Price (in / out) | Source |
|---|---|---|---|
| Moonshot direct (platform.kimi.ai) | yes | 2.00 / 10.00; a second Moonshot endpoint is listed at 4.00 / 20.00 | OpenRouter endpoints API (below); LiteLLM map `moonshot/kimi-k3.1-pro` 2e-06 / 1e-05 |
| OpenRouter `moonshotai/kimi-k3.1-pro` | yes. Listed 2026-10-23, 1,048,576 context | 2.00 / 10.00, cache read 0.20 | https://openrouter.ai/api/v1/models |
| Together (direct, and as an OpenRouter provider) | yes | 2.00 / 10.00 | https://openrouter.ai/api/v1/models/moonshotai/kimi-k3.1-pro/endpoints; LiteLLM `together_ai/moonshotai/Kimi-K3.1-Pro` |
| SambaNova | **no** | — | https://api.sambanova.ai/v1/models lists only Meta-Llama-3.3-70B-Instruct, MiniMax-M3.1-Flash, gemma-4-31B-it and gpt-oss-120b. It is also absent from the OpenRouter provider list. |

OpenRouter lists 18 provider endpoints. The cheapest: Relace fp4 at 0.40 / 7.88, then PrimaLabs, Wafer and InferenceNet at 1.80 / 9.00, and DeepInfra bf16 at 1.90 / 9.50 (75% uptime over the last 30 minutes). Most providers serve fp4 or mxfp4 quantizations, so cheaper does not mean the same model quality.

Third-party pages disagree on price. Respan lists 1.40 / 7.00 (https://www.respan.ai/models/kimi-k3.1-pro). I treated the OpenRouter and LiteLLM figures as authoritative.

## 2. Compared with current DeepSeek V4 routing

The fleet brain is `deepseek-v4-flash`. LiteLLM maps it to `deepseek/deepseek-flash` at api.deepseek.com (`research/models.yaml:234-239`). DeepSeek has retired the v4-flash name; requests now go to DeepSeek-V4.1-Flash-1017 (https://api-docs.deepseek.com/quick_start/pricing).

| Model | Input (cache miss) | Input (cache hit) | Output |
|---|---|---|---|
| deepseek-flash, off-peak / peak | 0.15 / 0.30 | 0.003 / 0.006 | 0.60 / 1.20 |
| deepseek-v4-pro, off-peak / peak | 0.66 / 1.32 | 0.022 / 0.044 | 1.98 / 3.96 |
| OpenRouter `deepseek/deepseek-v4-pro` (the gateway already has `openrouter-deepseek-pro`) | 1.60 | 0.135 | 3.20 |
| **Kimi K3.1 Pro** | **2.00** | **0.20** | **10.00** |

Against deepseek-flash at peak, Kimi costs 6.7x more on input and 8.3x more on output, and 33x more on cache hits. On agent loops that mostly hit the cache, the gap is wider than the headline ratio. Against the V4 Pro fallback, Kimi costs 1.5x more on input and 2.5x more on output (peak).

## 3. LiteLLM mapping

It already exists. The upstream `model_prices_and_context_window.json` (https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json) has these entries:
`moonshot/kimi-k3.1-pro`, `together_ai/moonshotai/Kimi-K3.1-Pro`, `baseten/moonshotai/Kimi-K3.1-Pro`, `gmi/moonshotai/kimi-k3.1-pro` and `opencode_go/kimi-k3.1-pro` (all 2e-06 / 1e-05), plus `kyma/kimi-k3.1-pro` (2.7e-06 / 1.35e-05).
For OpenRouter, the generic `openrouter/moonshotai/kimi-k3.1-pro` route works without a price-map entry. Whether the gateway's pinned LiteLLM image includes these entries is unverified: the gateway config is outside this worker's sandbox (same boundary as ISSUE-LOG #1069).

## 4. License

Source: https://huggingface.co/moonshotai/Kimi-K3.1-Pro/raw/main/LICENSE (HF metadata `license: other`, `license_name: kimi-k3.1-pro`).
The license is MIT-style with two added conditions:
- §2: a "Model as a Service" business with more than $20M revenue over 12 months needs a separate agreement with Moonshot.
- §3: a product with more than 100M MAU or more than $20M monthly revenue must display "Kimi K3.1-Pro" in its UI.
- §4 exempts internal use (nothing exposed to third parties) and any use through Moonshot's official products or certified inference partners.

Fleet routing is internal use, and an API route goes through a hosted provider. Neither condition applies. **Compatible.** One caveat: if a Lumen product ever exposes model output to outside users through a host that is not a certified partner, re-check §3. Moonshot direct is always exempt.

## 5. If we do add it

Leave the routing as it is for now. If an escalation tier is wanted later (for example a hard-ticket rung above deepseek-v4-pro), add it as a `cheap_cloud`-pool model, `status: DARK` until probed:

- gateway `model_name: kimi-k3.1-pro` → `litellm_params.model: openrouter/moonshotai/kimi-k3.1-pro`. This reuses the OpenRouter key the gateway already holds for `openrouter-deepseek-pro`, so no new credential is needed. Pin `provider.order` to Moonshot AI and Together so traffic doesn't land on the fp4 long-tail hosts.
- `models.yaml` row with `api_price_mtok: {in: 2.00, out: 10.00}`. Run the 3–5 ticket A/B against deepseek-v4-pro before flipping it to LIVE. The model-ref-lint MANIFEST check (ops/wq2903) only gates LIVE ids, so a DARK row is safe to land first.

Re-evaluate this decision if Kimi's price drops below about 1.0 / 4.0, roughly V4 Pro parity.
