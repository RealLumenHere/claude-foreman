# wq#1573 — glm-5.3-flash rung eval + instrumentation

Full report: `research/GLM53_FLASH_EVAL.md`.

## What's here

- `glm53flash_price_watchdog.py` — pages `lumen-fleet` (ntfy) if OpenRouter's
  live price for `z-ai/glm-5.3-flash` moves >25% (either direction, or the
  model vanishes from the catalog) vs the pinned 2026-09-09 baseline
  ($0.075/M in, $0.25/M out). Same edge-trigger pattern as
  `ops/wq1406-paid-fallback-guard/paid_spend_tripwire.py` (one page per
  distinct breach shape, not one per run).
  - Deploy: `glm53flash-price-watchdog.service` + `.timer` (every 6h; a
    promo-pricing repeal isn't as time-critical as a spend runaway, so this
    doesn't need the 30min cadence `paid-spend-tripwire` uses).
  - Tests: `tests/test_glm53flash_price_watchdog.py` (16 cases, pure
    functions, no live network/ntfy — `python3 tests/test_glm53flash_price_watchdog.py`).
- `litellm_cost_glm53flash_snippet.sh` — the exact block already appended to
  the live `/home/lumen/bin/litellm-cost` (that script lives outside this
  repo's git history, so the snippet is kept here for review/history).

## Rollback

- Watchdog: stop+disable the timer/service; it only reads a public catalog
  endpoint and writes state under `/home/lumen/agents/glm53flash-price-watchdog/`
  — no other side effects to unwind.
- Cost-report line: delete the appended `### GLM-5.3-FLASH` block from
  `/home/lumen/bin/litellm-cost`; the pre-existing "BY MODEL" section still
  covers the rung once it has spend, so nothing is lost by reverting.
- No workload routing was changed by this ticket (see report — held pending
  real usage data), so there is no routing rollback required yet. Per-rung
  rollback notes for when routing *does* move are recorded in the report's
  per-workload sections.
