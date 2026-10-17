# wq#4102: `model_group_alias` callers bypass `router_settings.fallbacks`

On 2026-10-16, `lumen/best` (alias for `deepseek-v4-pro`) returned DeepSeek's 402
`Insufficient Balance` with `No fallback model group found for original
model_group=lumen/best`. Calls to `deepseek-v4-pro` itself fell back to
`openrouter-deepseek-pro` and returned 200.

## Root cause (LiteLLM 1.83.10, the running version)

`Router.async_function_with_fallbacks` takes `original_model_group = kwargs["model"]`,
which is the name the caller sent (router.py:5341). `get_fallback_model_group`
(router_utils/fallback_event_handlers.py:45-82) matches that name against the
fallback keys in three ways: exact match, provider-stripped match, then `"*"`. It
never resolves `model_group_alias`. So an alias gets no fallback chain unless one is
keyed on the alias name itself, and that is why `lumen/judge` (a real `model_name`
with its own key) works. `validate_fallbacks` (router.py:1298) only checks the
shape of each entry, so a key that is an alias name is legal.

## Fix

`alias_fallbacks.py` copies each alias target's chain under the alias name. It does
this for every fallback kind present (`fallbacks`, `context_window_fallbacks`,
`content_policy_fallbacks`). An alias that already has its own entry is left alone.
The edit inserts lines into the existing lists. The rest of the file stays
byte-identical, and the result is re-parsed and checked against original +
additions before it is written. The write is in place, so the inode stays the same
for the single-file bind mount.

On the live config (applied 2026-10-17 01:20Z, backup
`/home/lumen/agent/litellm_config.yaml.bak-wq4102-20261017T012009Z`):

```
fallbacks:                + lumen/best -> [openrouter-deepseek-pro]
fallbacks:                + lumen/fast -> [cerebras-llama]
context_window_fallbacks: + lumen/fast -> [deepseek-v4-flash]
note: lumen/vision -> lumen-vision has no chain to inherit
```

`lumen/vision` behaves exactly like `lumen-vision`: there is no other vision-capable
group in `model_list`, so picking a fallback for it would be a new routing decision,
not part of this alias bug.

Re-run `alias_fallbacks.py check` after adding an alias or editing a chain. It exits
1 if any alias is missing a chain its target has.

## Reload

LiteLLM reads the config file only at process start, and its 10 s hot reload syncs
DB-stored settings only (proxy_server.py:4398). The edit is inert until litellm
restarts. Workers may not touch containers, so `reload_and_verify.sh` is queued as
lumen-window op `wq4102-litellm-alias-reload` (fleet window, pri 6). That puts it
after `wq3115-litellm-3cv6` (pri 1), whose upgrade or rollback recreates the
container. The op probes first and restarts only if the aliases still don't fall
back.

## Verify: force a primary failure, never stop a service

`probe_alias_fallback.py MODEL...` sends `mock_testing_fallbacks: true`. The router
then raises on the primary and walks the chain for that exact name. Baseline on the
running proxy, 2026-10-17, before the reload:

```
deepseek-v4-pro  200  model_group=openrouter-deepseek-pro  attempted_fallbacks=1
lumen/best       500  "mock exception for model=lumen/best ... No fallback model group found"
lumen/fast       500  "mock exception for model=lumen/fast ... No fallback model group found"
```

After the reload, all three must return 200 with `attempted_fallbacks=1`.

## Not fixed here

- DeepSeek's direct balance is exhausted (wq#3410), so every DeepSeek group is
  currently served through OpenRouter. Topping up is a purchase for Frank.
- The paid-fallback audit (`ops/wq1406-paid-fallback-guard/audit_fallback_chains.py`)
  already flags the six paid-only chains these aliases now mirror. Nothing new was
  introduced.

Tests: `python3 ops/wq4102-litellm-alias-fallbacks/tests/test_alias_fallbacks.py`
