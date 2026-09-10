# wq#1591 — register glm-5.3-flash pricing in model_info

D6 from `foreman/research/FLEET_COST_PLAN.md`. glm-5.3-flash is absent from
`/app/model_prices_and_context_window.json` in the litellm container, so spend
is currently recovered only from OpenRouter's returned `cost` field. That
works on the OpenRouter route but not on the Z.ai direct route
(`glm-5.3-flash-zai`) — if LPP traffic moves there before pricing is
registered, the budget governor and model-watchdog budgets stop seeing the
fleet's largest workload.

## What's here

- `litellm_config_snippet.yaml` — `model_info` block (input/output/
  cache-read cost per token) to hand-merge into both existing glm rungs'
  `model_list` entries in the live `/home/lumen/agent/litellm_config.yaml`.
  That file lives on the gateway node outside this repo's git history (same
  convention as wq#897/wq#948/wq#1299/wq#1406 — see
  `ops/wq1406-paid-fallback-guard/litellm_config_snippet.yaml` for the prior
  instance of this pattern), and this worktree has no read/write access to
  it, so this is a merge fragment, not a diff.
- Prices are Z.ai's published list rate: $0.15/M in, $0.03/M cached in,
  $0.50/M out. The $0.075/M-in promo baseline pinned in
  `ops/wq1573-glm53flash-eval/glm53flash_price_watchdog.py` ended 2026-09-09
  24:00 UTC+8, so list (not promo) is what belongs in `model_info` as of this
  merge.

## Deploy

1. Open `/home/lumen/agent/litellm_config.yaml` on the gateway node.
2. For the `model_name: glm-5.3-flash` and `model_name: glm-5.3-flash-zai`
   entries under `model_list`, add the `model_info` block from
   `litellm_config_snippet.yaml` alongside each entry's existing
   `litellm_params` — do not delete or recreate the entries.
3. Restart/reload the litellm proxy so the price map picks up the change.
4. Confirm via a `spend_key` or `/model/info` call that both rungs now report
   non-null `input_cost_per_token` / `output_cost_per_token` /
   `cache_read_input_token_cost`.

## Rollback

Remove the added `model_info` keys from both entries; LiteLLM falls back to
its bundled price map (which is where this rung was missing pricing in the
first place — spend recovery reverts to relying on OpenRouter's returned
`cost` field on that route only).
