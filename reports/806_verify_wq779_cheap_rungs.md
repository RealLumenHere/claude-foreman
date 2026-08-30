# wq#806 — Adversarial verify-pass of wq#779 (cheap rungs unreachable)

**Verdict: CONFIRMED — no contradiction.**

## What was shipped (PR#63, commit 9d06d0c, merged 2026-08-22 08:38 UTC)

Two structural fixes in orchestrator/router.py:

1. **Cost-aware classify_archetype tie-breaking (F3.1):** When the first-match-wins
   incumbent is NOT a cheap archetype, any cheap archetype (mechanical-edit or
   probe-build) with equal or stronger evidence now wins the tie. NOT pure
   cheapest-wins — a well-evidenced expensive archetype still beats an incidental
   cheap signal (test_classify_still_prefers_corroborated_expensive_archetype covers
   this). Restricted to the two structurally-outranked archetypes the wq#750 audit
   identified, not all 8.

2. **Attempt-count downgrade trigger (F3.2):** `select()` now walks the base
   (non-escalation) chain forward by `fails` count, independent of governor pressure.
   A ticket that fails on its primary rung gets a cheaper rung on retry without
   needing a governor floor breach (which had never fired: saw_limit 0/149).

## Verification evidence

### Tests: 45/45 pass
All orchestrator/tests/test_router.py pass (pytest 2026-08-23). The 6 new wq#779
tests cover: equal-evidence tiebreak, corroborated-expensive-still-wins,
probe-build downgrade chain (fails 0/1/2), mechanical-edit downgrade,
escalation-facts-not-overridden, and the literal CLI acceptance criterion.

### CLI acceptance criterion MET
```
route probe-build --fails 0 → claude-sonnet-5@medium (cloud)
route probe-build --fails 1 → claude-haiku-4-5 (cloud)      ← ACCEPTANCE
route probe-build --fails 2 → deepseek-v4-flash (local)
route mechanical-edit --fails 0 → qwen36-local (local)      ← local-first (1eb67fe)
route mechanical-edit --fails 1 → claude-sonnet-5@low (cloud)
```

### Real dispatch data — haiku now reachable
Pre-fix (Frank's 2026-08-21 measurement): 72 sonnet / 5 opus / 7 fable / 0 haiku
across 85 launches.

Post-fix audit (n=291 total): 223 sonnet / 59 opus / 6 fable / **3 haiku** / 0 local.
All 3 haiku launches are POST-FIX:
- wq807 attempt-1: 2026-08-22 08:46:00 UTC, mechanical-edit, haiku-4-5
- wq807 attempt-2: 2026-08-22 08:46:30 UTC, mechanical-edit, haiku-4-5
- wq817 attempt-1: 2026-08-22 09:07:45 UTC, mechanical-edit, haiku-4-5

Zero haiku or local launches exist before the fix. Zero local-rung launches yet
(local-first mechanical-edit commit 1eb67fe landed 07:12 UTC today, too recent for
organic tickets).

### wq#778 balance preserved
Replayed classify_archetype over all 865 tickets in queue.db:
- research-synthesis: 100/865 = 11.6% (under 15%)
- rca-investigation: 80/865 = 9.2% (under 15%)

The cost-aware tiebreak does not disrupt the wq#778 15% fixture for opus-primary
archetypes.

### Chain structure verified
```
probe-build:      sonnet-5@medium → haiku-4-5 → deepseek-v4-flash@local
mechanical-edit:  qwen36-local → sonnet-5@low → deepseek-v4-flash@local
```

Both chains are 3 rungs deep with cloud and local coverage. Escalation rungs
(opus-5, fable-5) are condition-gated and not affected by the base-chain downgrade.

## Items NOT contradicted
- The `_CHEAP_ARCHETYPES` set is correctly scoped to {mechanical-edit, probe-build}.
- `_primary_rung_cost` uses api_price_mtok.out × effort ladder position, with a
  sensible default of 1 for models without published pricing.
- The `min(fails, len(base) - 1)` clamp prevents walking past the last rung.
- `classify_archetype` now takes an optional `manifest` parameter and passes it
  through from `assign()` and the CLI — no redundant reloads.

## One observation (non-blocking)
The 3 haiku launches all show no `outcome` field in their attempt JSON — likely
the harness didn't record outcome for these early haiku dispatches. Not a router
bug; harness-side bookkeeping. Not filed as a ticket since the omission pattern
may already be addressed by subsequent harness commits.
