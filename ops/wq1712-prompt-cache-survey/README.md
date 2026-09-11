# wq#1712 — prompt-cache survey probes

Evidence behind `research/PROMPT_CACHING_SURVEY.md`. All read-only: four probe
scripts, no routing or config change. Total gateway spend across every run below
is under $0.02.

Each script needs the LPP-scoped gateway key
(`/home/lumen/.secrets/litellm-service-keys/lpp.key`), which a worker sandbox
cannot read (instinct 4 / `guard.sh`), so all of them run as the full `lumen`
user via `lumen-run`.

## Scripts

| script | question | output |
|---|---|---|
| `deepseek_cache_canary.ts` | does the rung's cache survive appending one turn? | `cache_canary_<model>.json` |
| `cost_header_probe.ts` | does the gateway price a cache hit differently at all? | `cost_header_<model>.json` |
| `solve_rates.ts` | what are the registered hit/miss/output rates? | `solved_rates_<model>.json` |
| `spend_accounting_probe.py` | what does `/model/info` register, and what did SpendLogs record? | `spend_accounting_probe.json` (401 — see below) |

`WQ1712_MODEL` selects the rung (default `deepseek-v4-flash`); `WQ1712_TURNS`
sets history length (default 175, which lands the prompt near LPP's measured
6,575-token average).

```
/home/lumen/bin/lumen-run start --name wq1712-dscanary -- \
  /usr/bin/env WQ1712_MODEL=deepseek-v4-flash \
  /home/lumen/pokemon/node_modules/.bin/tsx \
  /home/lumen/worktrees/wq1712-a1/ops/wq1712-prompt-cache-survey/deepseek_cache_canary.ts
/home/lumen/bin/lumen-run wait <UNIT> 180
```

## Results, one line each

- **deepseek-v4-flash** — 97.9% on an identical repeat, **93.7% with one turn
  appended**. Rates $0.014 / $0.44 / $1.32 per Mtok (hit / miss / output).
- **glm-5.3-flash-zai** — 99.2% identical, **11.1% appended**. Rates
  $0.03 / $0.15 / $0.50, which reproduces the Z.ai list prices FLEET_COST_PLAN
  §8.4 registered by hand.
- **cerebras-oss-120b** — 99.2% identical, **0% appended**. Rates
  $0.00 / $0.35 / $0.75 — the zero cache-read cost is an accounting hole, not a
  discount.

`cache_canary_deepseek-v4-flash.cold.json` is the first DeepSeek run, kept
because it is the only one whose `call1_cold` was genuinely cold;
`cache_canary_deepseek-v4-flash.json` is a later re-run that started against an
already-warm cache.

## Two probes that did not land cleanly

`spend_accounting_probe.py` gets **401 on both `/model/info` and `/spend/logs`**
with the key in `/home/lumen/.secrets/litellm-master.env`. The file parses
(`LITELLM_MASTER_KEY`, 41 chars) so the key is stale, not malformed. That is why
`solve_rates.ts` exists at all — it recovers the same rates from the
`x-litellm-response-cost` response header, which needs no admin scope. The 401
matters beyond this ticket: `ops/wq1406-paid-fallback-guard/paid_spend_tripwire.py:126`
reads the same file for the same endpoint, so the paid-spend alarm is likely
failing silently (ISSUE-LOG #322).

The probe's first version also 401'd for its own reason, worth recording because
the same shape is in the tripwire: `read_text().split("=", 1)[1]` swallows every
line after the first `=` when the env file has more than one entry. Fixed here
with line-wise parsing (`read_key()`).

## Rollback

Documentation and probes only; nothing here is wired into a unit or timer.
Deleting this directory plus `research/PROMPT_CACHING_SURVEY.md` is a full
revert.
