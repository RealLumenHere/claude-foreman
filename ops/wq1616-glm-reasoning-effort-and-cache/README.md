# wq#1616 — glm reasoning_effort pin + prefix-cache 0-hit investigation

FLEET_COST_PLAN.md §8.3 measured 275 live LPP turns on `glm-5.3-flash-zai`: avg
prompt 4,635 (plan assumed 6,575), avg completion 432 (plan assumed 117 — 11x
inflation), `cached_tokens` 0 on every request including direct probes to
api.z.ai. Two asks, both closed here.

## 1. Pin `reasoning_effort` low on the glm rungs

Landed as `pokemon` PR #110 (`fix/wq1616-glm-reasoning-effort-pin`, commit
`aa8c95d`): https://github.com/frankstupak/lumen-plays-pokemon/pull/110

`primary` was already pinned `low` via `envEffort("LPP_BRAIN_EFFORT", "low")`
(`pokemon/src/config.ts:45`). But `escalate`, `actRetry`, and `summarize` — all
three also routed through `glm-5.3-flash-zai` per `lpp-glm.env` — had no
`reasoningEffort` field at all, so every call on those legs rode the
provider's mandatory-reasoning default (uncapped, billed as output at
$0.50/M). `glm-5.3-flash-zai` rejects `reasoning_effort: none` with
"Reasoning is mandatory for this endpoint and cannot be disabled", so `low` is
the achievable floor.

Fix: added `reasoningEffort: envEffort("LPP_BRAIN_<LEG>_EFFORT")` to all three
profiles (`pokemon/src/config.ts:50,62,66` in the landed diff), same pattern
`primary` already used, no default (stays provider-default unless set).
Config-only; re-measure after `lpp-glm.env` sets the three new `_EFFORT=low`
vars and the unit restarts (ops task, outside the PR's diff).

## 2. Why `cached_tokens` is 0

Not a prompt-shape bug on our side. `wq#1592`'s canary
(`ops/wq1592-glm53flash-cache-canary/`) fired three byte-identical requests
2s/10s apart against `glm-5.3-flash` over OpenRouter and got `cached_tokens: 0`
on all three, including the exact repeats
(`ops/wq1592-glm53flash-cache-canary/canary_results.json`). A byte-identical
repeat inside 10s is the easiest possible case for any automatic prefix
cache; failing it there rules out `pokemon/src/prompt.ts`'s prefix stability
(already append-only/byte-stable) and rules out the 4,096 `max_tokens`
ceiling (max_tokens does not affect whether the input prefix is cached). That
leaves Z.ai's route not actually caching this model's traffic yet — it
reports the `cached_tokens`/`cache_write_tokens` fields in the usage payload
(the accounting plumbing exists) but never populates a nonzero value.

Logged as `kb/ISSUE-LOG.md` #274: vendor-side, not fixable from the LPP
codebase, OPEN pending a Z.ai support ticket or docs confirming prefix
caching is enabled for `glm-5.3-flash`.

## Rollback

Documentation only; the code change lives in the separate `pokemon` repo PR
#110 above and reverts independently. Deleting this directory is a full
revert of this repo's artifact.
