# wq#1592 — glm-5.3-flash prompt-cache canary

D5 from `research/FLEET_COST_PLAN.md`, order-of-work step 3. Full report:
`research/GLM53_FLASH_CACHE_CANARY.md`.

## What's here

- `canary.ts` — imports the real `pokemon/src/prompt.ts` assembly
  (`SYSTEM_PREFIX`, `History`, `buildMessages`, `renderState`) and fires
  LPP's actual prompt shape at `glm-5.3-flash` over the OpenRouter gateway
  route three times with a byte-identical prefix, logging
  `prompt_tokens_details.cached_tokens`, `cache_write_tokens`, and
  `completion_tokens`/`reasoning_tokens` from each response.
- `canary_results.json` — the run's output (this worktree's own sandbox
  cannot reach the LPP gateway key, so the run happened via
  `/home/lumen/bin/lumen-run` as the full `lumen` user; see script header).

## Rerun

```
/home/lumen/bin/lumen-run start --name wq1592-canary -- \
  /home/lumen/pokemon/node_modules/.bin/tsx \
  /home/lumen/worktrees/wq1592-a1/ops/wq1592-glm53flash-cache-canary/canary.ts
/home/lumen/bin/lumen-run wait <UNIT> 60
```

## Rollback

Read-only measurement, no routing/config changed. Deleting this directory
and the report is a full revert.
