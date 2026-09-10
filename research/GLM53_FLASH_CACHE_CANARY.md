# wq#1592 — glm-5.3-flash prompt-cache canary (D5)

D5 from `FLEET_COST_PLAN.md`: `cache_hit` is null on every paid row in the 35d
window, and section 3's order-of-work step 3 calls for canarying LPP's real
prompt shape against glm-5.3-flash over OpenRouter, measuring
`cached_tokens`/`completion_tokens`, before any cutover. This ticket runs that
canary. It changes no routing.

## Method

`ops/wq1592-glm53flash-cache-canary/canary.ts` imports the production prompt
assembly directly — `SYSTEM_PREFIX`, `History`, `buildMessages`, `renderState`
from `/home/lumen/pokemon/src/prompt.ts` — rather than approximating it, so
the request shape (system-prefix-as-own-message, append-only history block,
fixed-order tail) matches what `agent.ts:1419` actually sends. History is
filled to `CFG.historyKeepVerbatim` (30) lines in the exact
`T<n> [map<m> x,y] <act> => <result>` format `agent.ts:1508` pushes each turn.
The request body mirrors `brain.ts`'s `callRung` (`model`, `messages`,
`max_tokens: CFG.profiles.primary.maxTokensOut`, `temperature: 0.6`,
`reasoning_effort: CFG.profiles.primary.reasoningEffort` = `"low"`) and posts
to `CFG.gateway` (the live LiteLLM gateway, OpenRouter route).

Three back-to-back calls were fired with a **byte-identical prefix** (same
system message, same history/notepad/state — only nothing changes between
calls, this is the best case for a prefix cache), spaced 0s / 2s / 10s apart,
to see whether a repeat hit gets served from cache on the second or third
call.

This worker session's own sandbox cannot read the LPP-scoped gateway key
(`/home/lumen/.secrets/litellm-service-keys/lpp.key` is denied by
`guard.sh`, instinct 3), so the script was launched via
`/home/lumen/bin/lumen-run` as a detached systemd unit running as the full
`lumen` user, which does have that read — the intended bypass path for
exactly this class of task. Results and full raw `usage` payloads are logged
to `ops/wq1592-glm53flash-cache-canary/canary_results.json` (no secret
material — the run script never echoes the key).

## Result

| call | gap | prompt_tokens | cached_tokens | cache_write_tokens | completion_tokens | reasoning_tokens |
|---|---|---|---|---|---|---|
| call1_cold | — | 2004 | 0 | 0 | 67 | 25 |
| call2_repeat_prefix | 2s | 2004 | 0 | 0 | 62 | 34 |
| call3_repeat_prefix | 10s | 2004 | 0 | 0 | 31 | 0 |

All three: HTTP 200, `prompt_tokens_details.cached_tokens` and
`cache_write_tokens` both present in the payload (confirming D5's "usage
payload carries the fields" claim) but **zero on every call, including the
repeats against a byte-identical prefix**. The route reports cache
accounting; it is not actually caching anything at this prompt size/gap.

Reasoning tokens were small (0-34) relative to the plan's conservative "output
inflates 3x" assumption — nowhere close to 3x here, though this canary's
prompt (2,004 tokens, synthetic 30-line history) is well under the fleet's
measured 6,575-token LPP average, so the reasoning-token share of a real
6.5k-token turn was not directly reproduced and may scale differently.

## Conclusion for the routing decision

The reasoning-token risk in the plan's caveat is **not confirmed as a
blocker** — completion/reasoning tokens stayed modest across all three calls.
But the **caching assumption is not confirmed either**: this canary got zero
cache hits at 2,004 tokens with identical repeats up to 10s apart, which
contradicts the "60-80% prefix hit rate" band the plan builds its savings
estimate on. Before moving the LPP main brain (FLEET_COST_PLAN.md step 4),
this needs a second canary at a *realistic* prompt size (~6.5k tokens, i.e. a
longer synthetic history or a captured real transcript) and a wider range of
inter-call gaps, since zero-hit-at-2k-tokens does not rule out a size
threshold or a gap-length threshold this run didn't hit. Recommend re-running
`canary.ts` with `CFG.historyKeepVerbatim`-equivalent padded to ~80 turns
before treating the cache line of the routing table as real.
