# wq#2471 — gemini-flash / nephthys-qwen14b: no fallback group, retries exhaust with no landing

Merge fragment: `litellm_config_snippet.yaml`. Same hand-merge convention as
wq#897/wq#948/wq#1295/wq#1406/wq#1590/wq#2627 — the live LiteLLM proxy config
(`/home/lumen/agent/litellm_config.yaml`) is NOT in this repo; it lives on the
gateway node.

## Root cause

`docker logs litellm --since 24h` shows two model groups retrying 3x on
RateLimitError/connection failure and then erroring with "No fallback model
group found for original model_group=<x>" instead of landing on a working
rung:

- `gemini-flash` (114x): free-tier `gemini-2.5-flash` is 20 RPD; bursts of 6
  every 4-10 min blow through that fast. `router_settings.fallbacks` has no
  entry for `gemini-flash` at all, so every exhausted retry is a hard error.
  wq#2627 already proposed this exact fallback (`gemini-flash ->
  cerebras-oss-120b`, PART 1 of `ops/wq2627-gemini-litellm/`) as an
  immediately-deployable fix, but per wq#2065's 24h log window it had not yet
  been merged into the live config. This ticket adopts that same PART 1 entry.

- `nephthys-qwen14b` (18x): this is NOT the retired MCP brain
  `nephthys-qwen3-14b` (RETIRED wq#589 2026-08-22, `research/models.yaml`
  "nephthys-qwen3-14b" entry, note field cites the 2026-08-23 unreachable-host
  probe). It is a distinct, still-LIVE local family — same box, different
  serving role — declared in `research/inference_nodes.yaml:31` and rendered
  into `research/models.yaml` (`id: nephthys-qwen14b`, `status: LIVE-local`).
  `dispatcher/config.json:10` dispatches to it directly by name (outside the
  `local-worker` group at `dispatcher/config.json:12`), so a LiteLLM-level
  RateLimit/connection failure on it has nowhere to land: no fallback entry
  exists for it either, even though `qwen36-local` is a sibling member of the
  same `local-worker` group and is the natural landing rung the ticket asks
  for ("Neither reaches qwen36-local").

No decommissioned model is actually being called — the naming collision
between `nephthys-qwen14b` (live) and `nephthys-qwen3-14b` (retired) is a
false alarm worth recording so it isn't re-investigated as a dead-reference
bug later.

## Fix

Both model groups get an explicit terminal fallback so a retry-exhausted
request lands on a working rung instead of erroring:

    router_settings:
      fallbacks:
        - gemini-flash: [cerebras-oss-120b]
        - nephthys-qwen14b: [qwen36-local]

## Deploy (operator, on the gateway node)

1. `cp /home/lumen/agent/litellm_config.yaml /home/lumen/agent/litellm_config.yaml.bak-$(date +%s)`
2. VERIFY fallbacks key location for the running LiteLLM version
   (`router_settings.fallbacks` on recent proxies, `litellm_settings.fallbacks`
   on older ones — same check wq#2627 documented) and hand-merge the two
   entries above, appending to any existing list.
3. `systemd-run --unit wq2471-litellm-restart --collect docker restart <litellm-container>`
4. `curl -s http://localhost:4000/health/readiness` — expect 200.
5. Re-check `docker logs litellm --since 1h | grep -c 'No fallback model group'`
   trends to 0 over the next few bursts.
