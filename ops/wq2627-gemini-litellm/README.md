# wq#2627 — Gemini paid-tier LiteLLM wiring: deploy runbook

Research + decision record: `research/GEMINI_ACCESS.md`. Merge fragment:
`litellm_config_snippet.yaml` (PART 1 = fallback fix, deployable now; PART 2 =
paid rungs, needs the key). Closes wq#2580.

## Order of operations

### 0. Frank's one physical act (PART 2 prerequisite; PART 1 does not wait for it)
In aistudio.google.com: pick/create the API project → link a billing account
(instant usage Tier 1) → mint an API key. On the gateway node, drop it into the
LiteLLM env (0600, path-only handling — never echo it, never in a URL):

    # in the env file the litellm service already loads (VERIFY which one on the
    # gateway node — same file that holds the existing provider keys)
    GEMINI_API_KEY_PAID=<key>

Do NOT reuse the old free-tier key var: keeping the paid key separate means the
old free key can be dropped/rotated independently, and a bad merge fails loud
(missing env) instead of silently landing on the 20-RPD free quota.

### 1. Backup + merge
Follow the wq#1295 convention: timestamped backup of
`/home/lumen/agent/litellm_config.yaml`, then hand-merge the snippet.
- PART 1 (`router_settings.fallbacks`): VERIFY key location for the running
  LiteLLM version (recent proxies: `router_settings.fallbacks`; older:
  `litellm_settings.fallbacks`). Append to any existing list.
- PART 2 (`model_list`): replace the existing `gemini-flash` entry in place
  (same model_name — its consumers keep working); append the two new entries.

### 2. Restart + verify (detached systemd-run per instinct 5 — gateway is on the
Commander path)

    # all ON the gateway node (SSH-wrapped if issued from elsewhere)
    systemd-run --unit wq2627-litellm-restart --collect docker restart <litellm-container>
    curl -s http://localhost:4000/health/readiness          # expect 200
    # authed model probe (key from the 0600 env file, never inline):
    # POST /v1/chat/completions model=gemini-flash, then model=gemini-pro-research
    # expect 200s; a 401/400 means the env var didn't reach the container.

### 3. Post-deploy checks
- SpendLogs over the next 24h: gemini-flash 429 rate should drop from ~51% to ~0.
- `lumen-model-watchdog` picks up the two new rungs automatically if its roster
  is model-list-driven; if the wq#1295 `health_check_models` cap is deployed,
  re-run `health_check_model_filter.py` to regenerate the capped roster (the new
  rungs are cheap-cloud class, includable).
- Flip `research/models.yaml` `gemini-3-flash` row status DARK → LIVE-local (the
  row ships DARK so nothing routes to it before the key exists; DARK rows are
  excluded from the pool sets by `harness/concurrency.py:_pool_models`). The flip
  re-adds it to CHEAP_CLOUD_MODELS/LOCAL_POOL_MODELS, so it must follow the
  wq#2250 single-source procedure: add `gemini-3-flash` to `governor_prefixes` in
  `research/inference_nodes.yaml` and to the five LOCAL_POOL_MODELS literals
  (`fworker`, `governor_core`, `orchestrator.judgment`, `despag.admission`,
  `despag.app`) in the same commit — `harness/tests/test_prefix_list_parity.py`
  enforces the round trip and will name any site missed.

## What NOT to do
- Do not wire Gemini CLI / Antigravity OAuth credentials into the gateway —
  Google suspends accounts for third-party use of that quota (GEMINI_ACCESS.md
  §(a)). CLI OAuth is for Frank's own terminal only.
- Do not scrape gemini.google.com sessions (ToS + ban risk; explicit in wq#2627).
