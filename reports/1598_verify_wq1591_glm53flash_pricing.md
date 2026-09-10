# wq#1598 — verify-pass: wq#1591 register glm-5.3-flash pricing in model_info

**Verdict: CONFIRM** — the shipped merge fragment carries correct list prices, correct
per-token conversions, correct LiteLLM `model_info` field names, and correct model names for
both glm rungs, and is honest about the one thing it cannot do (write the live gateway config).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/390 (merged as `46de730`, merge commit
`e85169d`, both already on `origin/main`).

## What the diff does

Adds `ops/wq1591-glm53flash-pricing/{README.md,litellm_config_snippet.yaml}` — a YAML fragment
carrying a `model_info` block (`input_cost_per_token`, `cache_read_input_token_cost`,
`output_cost_per_token`) for both `glm-5.3-flash` (OpenRouter route) and `glm-5.3-flash-zai`
(Z.ai direct route) — plus updates `evidence.json`'s AC1 to cite the new file. No code touches
the actual gateway config (`/home/lumen/agent/litellm_config.yaml`), which lives outside this
repo and outside this worktree's `--add-dir` roots; the PR says so explicitly and ships a
hand-merge fragment instead, matching the established convention for that file (wq#897/948/
1299/1406 — checked `ops/wq1406-paid-fallback-guard/litellm_config_snippet.yaml`, same
disclaimer, same shape).

## Verification performed

1. **Read the diff cold**: `git -C /home/lumen/foreman show 46de730`. Three files changed,
   all in scope; `evidence.json` citation-only (no `cmd_run`/`exit_code`), matching the
   existing repo convention for config-only, non-runnable deliverables (checked
   `git show d7048b0:evidence.json` for wq#1406's equivalent — same citation-only shape).
2. **Cross-checked the prices against two independent in-repo sources**, not just the ticket
   text:
   - `research/FLEET_COST_PLAN.md:170,178-181` (D6's own source): "$0.15/M input, $0.03/M
     cached input, $0.50/M output" list, promo ($0.075/M in) "ends at 24:00 on 2026-09-09
     (UTC+8)". Exact match to the PR's values and rationale.
   - `ops/wq1573-glm53flash-eval/glm53flash_price_watchdog.py:5,38-39`: independently pins the
     *promo* baseline as `$0.075/M in, $0.25/M out`. List = 2x promo on both legs
     ($0.075→$0.15, $0.25→$0.50), an internally consistent multiplier — corroborates the PR's
     $0.50/M output figure from a second, independently-written source rather than just
     echoing the ticket body.
   - The watchdog's own *test fixture* (`tests/test_glm53flash_price_watchdog.py:40`) uses
     `completion: 0.00000025` ($0.25/M) — at first glance this looked like a contradiction of
     the PR's $0.50/M, but it's synthetic unit-test data for `pct_move`/`find_model_pricing`
     logic (arbitrary numbers to exercise the parser), not a pricing assertion. Confirmed by
     reading the test class: it only checks parsing math, never compares against $0.50.
3. **Per-token math**: `$0.15/M → 0.00000015`, `$0.03/M → 0.00000003`, `$0.50/M → 0.0000005`.
   All three divisions correct (`price / 1e6`).
4. **YAML validity + structure**: `python3 -c "import yaml; yaml.safe_load(...)"` on the shipped
   snippet — parses clean, both `model_name` entries present with all three `model_info` keys,
   values round-trip as the expected floats (`1.5e-07`, `3e-08`, `5e-07`).
5. **Field-name check**: `input_cost_per_token` / `output_cost_per_token` /
   `cache_read_input_token_cost` are LiteLLM's real `model_info` schema keys (not invented);
   also verbatim-match the field names named in wq#1591's own ticket body.
6. **Model-name check**: `glm-5.3-flash` / `glm-5.3-flash-zai` match the names used for the
   same two rungs in `research/FLEET_COST_PLAN.md:94,184` (D1's Z.ai-direct-rung discussion) —
   internally consistent, though neither this PR nor this verify pass can read the live
   `model_list` on the gateway node to confirm those are the exact strings deployed there (see
   below).
7. **Promo-expiry arithmetic**: promo end "2026-09-09 24:00 UTC+8" = 2026-09-09 16:00 UTC;
   merge commit timestamp is 2026-09-10 05:07:08 UTC, after expiry — list pricing is correctly
   what's in effect at merge time.

## Not a contradiction, noted for completeness

- **No live-gateway check possible.** wq#1598's own capability lint tagged `needs: ssh(anubis)`,
  but `ssh anubis` is not on the BUILD-lane per-command allowlist regardless of the lint tag —
  same structural gap already logged as `kb/ISSUE-LOG.md` #256 (wq#1556 verify, same "capability
  lint names a tool no lane profile grants" class). Did not retry it (wq#1107 loop-breaker
  lesson: a blocked shape doesn't become allowed on a second try). This also isn't the right
  host anyway — nothing in-repo ties `anubis` to the LiteLLM gateway; `research/
  BIN_TOOL_AUDIT.md`/`BROKER_GRANT_COVERAGE.md` place `anubis` among the generic peripheral
  fleet nodes, not the gateway. Verified everything else reachable instead, consistent with the
  #256 precedent.
- **No test file shipped.** Reasonable for a pure-data config fragment (three numbers x two
  rungs) with no branching logic to unit-test — matches wq#1406's own precedent, which also
  shipped a config-only snippet with no accompanying test.
