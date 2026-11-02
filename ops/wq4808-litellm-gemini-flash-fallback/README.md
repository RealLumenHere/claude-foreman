# wq#4808: `gemini-flash` has no LiteLLM fallback — 429 hard-fails instead of landing on a working rung

Merge fragment: `litellm_config_snippet.yaml`. Same hand-merge convention as
wq#897/wq#948/wq#1295/wq#1406/wq#1590/wq#2627/wq#2471/wq#4102/wq#4135 — the
live LiteLLM proxy config (`/home/lumen/agent/litellm_config.yaml`) is NOT in
this repo; it lives on the gateway node and is deliberately outside a build
worker's readable/writable paths (credential-bearing — guard.sh denies even a
`Read`).

## Root cause

`router_settings.fallbacks` has no entry keyed `gemini-flash`. The Gemini
free tier is 20 RPD on `gemini-2.5-flash` (research/models.yaml:271, wq#2580
measured 51% 429s at that volume), so once the daily quota is blown, every
request returns `No fallback model group found for original
model_group=gemini-flash` instead of landing on a working rung — other
groups (`deepseek-v4-flash`, `lumen-lfm2-1b`, `deepseek-v4-pro`, ...) already
have fallback entries; this one never got one.

This isn't a new bug: wq#2471 (ops/wq2471-litellm-429-fallback/README.md) and
wq#2627 (ops/wq2627-gemini-litellm/) both already identified it and both
proposed the same fix, `gemini-flash -> cerebras-oss-120b`. Neither fragment
ever landed in the live config (that's why the bug is still open), and in
the time since, landing it as written would have been wrong anyway:
wq#4135's probe table shows every Cerebras rung (`cerebras-llama`, same
account/billing as `cerebras-oss-120b`) returning 402 Payment Required since
the 2026-08-19 Cerebras billing lapse. A fallback onto a dead rung still
500s, so this ticket replaces the target rather than just re-proposing the
same fragment.

## Fix

```yaml
router_settings:
  fallbacks:
    - gemini-flash: [deepseek-v4-flash]
```

`add_fallback.py` is wq#4102/wq#4135's safety pattern generalized to adding
one new explicit fallback key (as opposed to mirroring an alias or swapping a
dead rung): line-level insertion, so the rest of the file stays
byte-identical; the result is re-parsed and compared against original + the
new entry before anything is written; the write is in place (the gateway
bind-mounts this single file, which pins the inode). An existing entry for
the group is left alone, not duplicated — see
`tests/test_add_fallback.py::test_existing_group_left_alone_not_duplicated`.

Implementation: `add_fallback.py:117-127` (`build()` — plan, render, re-parse-
and-compare, return the new text) backing `add_fallback.py:130-171` (`main()`
— the `check`/`apply --write` CLI).

### Chain depth, headroom, capability match (ticket's verification list)

- **Depth ≤ 2, no cycle.** `deepseek-v4-flash` already has its own fallback
  entry, `deepseek-v4-flash: [openrouter-free]` (present in the committed
  fixture used by the tests, and per wq#4135's live probe — direct DeepSeek
  balance is exhausted, wq#3410, so it only answers 200 via that fallback
  today). So the full walk is `gemini-flash -> deepseek-v4-flash ->
  openrouter-free`: two hops, terminal at a rung that itself has no further
  fallback, so no cycle is possible. Verified mechanically in
  `tests/test_add_fallback.py::test_chain_depth_two_no_cycle`, which walks
  the chain in the merged config and asserts no group repeats.
- **`openrouter-free` headroom.** It's already the shared fallback terminal
  for four other groups (`deepseek-v4-flash`, `deepseek-chat`,
  `gpt-3.5-turbo`, `messenger-chat`, per wq#4135) plus, transitively,
  `lumen/fast`/`lumen-lfm2-1b`. Gemini's own free-tier ceiling is 20 RPD
  (research/models.yaml:271), so even a fully-exhausted day adds at most 20
  requests/day of overflow onto a rung already carrying that traffic — the
  ticket's own framing ("the fallback absorbs almost nothing... a
  correctness fix more than a capacity fix") matches what's actually being
  added.
- **Model-capability match.** `gemini-flash` is used only as a text/chat
  judge or research seat (`tools/council/council.yaml:24`,
  `tools/council/council.py`, the `minerva_verify.py` judge callers across
  several `ops/*` dirs) — grepped the repo for `gemini-flash` and found no
  vision/multimodal route. The only vision-capable LiteLLM group is
  `lumen-vision` (a separate `model_name`, `model_info.supports_vision:
  true` in the wq#4102 fixture), which this entry does not touch. Nothing to
  exclude.
- **Category-aware firing (quota/rate_limited/error, not auth/billing).**
  Out of scope for this entry: `router_settings.retry_policy` already sets
  `AuthenticationErrorRetries: 0` / `BadRequestErrorRetries: 0` (present in
  every fixture under `ops/*/tests/fixtures/litellm_config.yaml`), so
  auth/billing-class errors aren't retried into a fallback attempt in the
  first place. The ticket note flags that making this fully category-aware
  depends on the selfheal classify fix (sibling ticket, `ops/wq3354-fleet-
  selfheal/sh_providers.py`); this ticket only adds the missing routing
  entry and doesn't touch that classifier.

## Deploy (fleet-window op, not a build-worker action)

Workers don't touch the live config file or restart containers directly
(same restriction wq#4102/wq#4135 note, and the one this session hit first-
hand: `guard.sh` denies even a `Read` of `/home/lumen/agent/
litellm_config.yaml` — "literal Langfuse secret inside, wq#2951"). Queue
`reload_and_verify.sh` as a fleet-window op (node lumen):

1. Precheck: `add_fallback.py check CONFIG --group deepseek-v4-flash`, then
   probe that `deepseek-v4-flash` itself answers (direct or via its own
   fallback) — refuse to point `gemini-flash` at a rung that's currently
   dead.
2. `add_fallback.py apply CONFIG --group gemini-flash --to deepseek-v4-flash
   --write` (idempotent — a second run is a no-op per
   `test_idempotent`).
3. `add_fallback.py check CONFIG --group gemini-flash` to confirm the entry
   landed.
4. Probe `gemini-flash` with `wq4102's probe_alias_fallback.py
   gemini-flash` (`mock_testing_fallbacks: true`, forces the primary to
   raise and walks the real chain). If it already falls back, skip the
   restart. LiteLLM only reads the config file at process start
   (wq#4102 README), so the edit is otherwise inert.
5. Restart `litellm` via `docker compose restart`, wait for
   `/health/readiness`, re-probe `gemini-flash` — must return 200 with
   `x-litellm-attempted-fallbacks: 1`.

Tests: `python3 -I ops/wq4808-litellm-gemini-flash-fallback/tests/test_add_fallback.py`
(6/6 pass, against the committed live-shape fixture
`ops/wq4102-litellm-alias-fallbacks/tests/fixtures/live_router_settings_20261017.yaml`,
which has no `gemini-flash` key and already has
`deepseek-v4-flash: [openrouter-free]`).
