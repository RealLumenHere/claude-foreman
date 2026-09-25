# Fleet LLM cost plan

Analysis and recommendation only. Nothing in here has been applied.

Source: `LiteLLM_SpendLogs` in the `litellm` database on `lumen-pgvector`.
Window pulled: 2026-08-06 04:47 to 2026-09-10 04:46 UTC (35 days), 307,242 rows.
Written 2026-09-09 by session `work-cost-plan-60a32b`.
Query files kept at `/home/lumen/scratch/costplan/q1..q6.sql` and `derive.py`.

**Scope caveat, added 2026-09-25 (wq#2664).** `LiteLLM_SpendLogs` only sees traffic that
passes through the LiteLLM gateway. mailbot (a Cloudflare Worker, `mailbot/wrangler.toml`)
calls `api.deepseek.com` directly and never touches the gateway, so its spend is not in
the $107.31 total, §1's table, or any figure below except §8, which prices it separately
from code reads rather than from measured spend logs. Grade **B/C** there, not **A** —
see §8 for why.

## 1. Where the money actually goes

### 35-day totals

Total spend over the 35-day window is **$107.31**, not ~$98. DeepSeek is
**$80.26** of it (v4-flash $64.70, v4-pro $15.38, deepseek-chat $0.18), so the
call that DeepSeek dominates is right; the split is 75% rather than 85%.

| rung | reqs | spend | pct | prompt tok | completion tok | $/Mtok blended |
|---|---|---|---|---|---|---|
| deepseek-v4-flash | 159,474 | 64.70 | 60.3% | 262.3M | 33.0M | 0.219 |
| deepseek-v4-pro | 8,720 | 15.38 | 14.3% | 13.5M | 7.0M | 0.751 |
| cerebras-oss-120b | 18,035 | 14.04 | 13.1% | 50.7M | 5.0M | 0.252 |
| mistral-small | 37,341 | 9.34 | 8.7% | 82.3M | 1.7M | 0.111 |
| cerebras-llama | 3,265 | 1.58 | 1.5% | 0.8M | 1.8M | 0.615 |
| openrouter-free | 1,626 | 1.07 | 1.0% | 2.5M | 3.4M | 0.180 |
| gemini-flash | 900 | 0.72 | 0.7% | 1.6M | 0.1M | 0.411 |
| everything else | 76,000 | 0.48 | 0.4% | 57.3M | 5.0M | ~0 |

### The model is not the story, the workload is

Spend is not spread across the fleet. One workload owns it.

| key_alias | 35d spend | pct |
|---|---|---|
| lpp-2 | 54.24 | 50.5% |
| (unaliased `1bd4635b5896…`) | 23.48 | 21.9% |
| lpp-cerebras | 10.00 | 9.3% |
| ops-emergency-3 | 7.61 | 7.1% |
| lumen-agent-2 | 5.02 | 4.7% |
| minerva-pipeline-2 | 1.87 | 1.7% |
| ops-emergency-2 | 1.79 | 1.7% |
| all others | 3.30 | 3.1% |

The unaliased key stopped spending the week of 2026-08-24 and its metadata
carries the aliases `lumen-agent`, `ops-emergency`, `minerva-pipeline`. It is the
pre-rotation generation retired by wq#949 on 2026-08-26, not a separate consumer.
Its spend is historical.

### Forward run-rate (post-rotation only)

Because of that rotation, the honest baseline is the 14 days from 2026-08-27:

**$79.19 over 14 days = $169.68/month.** LPP is **$64.23 of it, 81%.**

| workload | rung | reqs/14d | $/14d | $/mo | avg in | avg out |
|---|---|---|---|---|---|---|
| lpp-2 | deepseek-v4-flash | 27,730 | 45.40 | 97.28 | 6,575 | 39 |
| lpp-cerebras | cerebras-oss-120b | 7,245 | 9.99 | 21.41 | 6,085 | 176 |
| lpp-2 | mistral-small | 11,884 | 8.79 | 18.84 | 6,787 | 34 |
| ops-emergency-3 | deepseek-v4-pro | 895 | 5.80 | 12.42 | 2,236 | 1,162 |
| lumen-agent-2 | deepseek-v4-flash | 945 | 3.07 | 6.57 | 1,743 | 1,984 |
| ops-emergency-3 | deepseek-v4-flash | 1,571 | 1.57 | 3.36 | 1,388 | 536 |
| minerva-pipeline-2 | deepseek-v4-flash | 1,701 | 1.17 | 2.50 | 3,485 | 152 |
| lumen-agent-2 | cerebras-oss-120b | 547 | 1.16 | 2.49 | 2,067 | 2,236 |
| lumen-agent-2 | openrouter-free | 644 | 0.53 | 1.14 | 1,832 | 2,443 |
| minerva-pipeline-2 | gemini-flash | 231 | 0.43 | 0.91 | 5,937 | 87 |
| model-watchdog | 4 rungs | 7,369 | 0.31 | 0.66 | 6-85 | 1-5 |
| everything else | | | 0.99 | 2.10 | | |

### The cost driver is input tokens

Fleet-wide over the window: 461M prompt tokens against 57M completion tokens.
**89% of billed tokens are input.** For LPP specifically it is 307.1M input
against 2.8M output, or **99.1% input**. Nothing about output budgets or model
choice moves the needle as much as what happens to the prompt.

### Spend is bursty, not steady

Baseline is $0.70-$1.50/day. Four days carry $57 of the $107:
08-29 $8.04, 09-01 $13.60, 09-02 $24.43, 09-09 $11.12. Every one of them is an
LPP run. The 09-01/09-02 pair is lpp-2 on deepseek-v4-flash (21,812 requests,
$36.04); 08-29 is the same workload on mistral-small; 09-09 is lpp-cerebras on
cerebras-oss-120b. Same prompt shape each time, different rung.

That matters for how to read the run-rate: $169.68/mo assumes LPP keeps running
at the current session cadence. It is a session-count number, not a background
burn.

## 2. Defects found while measuring

These are cost leaks and availability problems, not opinions. Each one needs a
ticket.

**D1. The Z.ai direct rung has never worked.** `glm-5.3-flash-zai` is at 99.1%
failure (348 of 351 requests). Probed the endpoint directly: `HTTP 401
{"code":"1000","message":"Authentication Failed"}`. `ZAI_API_KEY` is present in
the litellm container env but the value is not a valid Z.ai key. Every request to
that rung falls through to paid Cerebras. This is the blocker for the whole plan
below, and the fix needs a credential only Frank can mint (`cord request`).
Filed as wq#1587; already independently re-confirmed the same 401 live.

**D2. Circular fallback between the free rung and a paid rung.** `router_settings`
has `qwen36-local -> cerebras-oss-120b` and `cerebras-oss-120b -> qwen36-local`.
With `num_retries: 3`, a failure on the free local rung lands on paid Cerebras and
vice versa. 33,292 qwen36-local failures in the window. Proof it is spending:
`foreman-local-rung` shows $0.083 of spend on a rung whose price is zero, at
952,449 prompt tokens, which prices out almost exactly at Cerebras input rates.

**D3. qwen36-local failures are content-type rejections, not capacity.** The
actual errors are `Content type 'image_url' is not supported by selected model`
and `reasoning_effort: Input should be 'none','low','medium' or 'high'`, both
raised as `CerebrasException` after the fallback. So the free rung cannot be
loaded with LPP traffic today because LPP sends multimodal turns and the fallback
target is text-only. Fix the routing before moving any workload there.

**D4. deepseek-v4-flash is at 59.2% failure.** 94,339 failures in 35 days, cause
`Cannot connect to host api.deepseek.com:443`. Failed attempts cost nothing
directly, but they drive the retry and fallback machinery that does cost, and a
59% failure rate on the fleet's primary rung is its own problem.

**D5. No prompt caching is active anywhere.** `cache_hit` is null on every paid
row in the window. Sampled the stored response payloads for deepseek rungs over 7
days: zero rows carry `prompt_cache_hit_tokens`. Whatever DeepSeek's server-side
cache is doing, the fleet has no evidence of it and no accounting for it.

**D6. glm-5.3-flash is absent from LiteLLM's price map.** Verified against
`/app/model_prices_and_context_window.json` in the container. Spend is currently
being recovered from OpenRouter's returned `cost` field, which works on the
OpenRouter route but will not on the Z.ai route. If LPP moves to glm without
`input_cost_per_token` / `output_cost_per_token` / `cache_read_input_token_cost`
in the rung's `model_info`, fleet spend goes invisible and the budget governor and
model-watchdog budgets stop seeing it.

**D7. `mistral-small` is gone from the config but carried $8.79 in the window.**
Last spend 2026-08-29 21:00. It was the cheapest rung on the board at $0.106/M
input measured, and LPP moved off it onto deepseek-v4-flash at $0.208/M input.
That move roughly doubled the input rate on the fleet's biggest workload.

## 3. Prompt-caching opportunity

This is where most of the money is.

### The prompt is already built for it

`/home/lumen/pokemon/src/prompt.ts` opens with "Cache-stable prompt assembly" and
follows the rules: byte-stable system prefix, append-only history, deterministic
fixed-order state rendering, screenshots never entering history. `buildMessages`
puts `SYSTEM_PREFIX` in its own system message and orders the user block as
history, then guidance, then persona, then notepad, then fresh state last.

That is the correct shape. Between turn N and N+1 the shared prefix is the system
message plus the history block, and only the tail changes. History is append-only
until compaction at 80 turns (`historyKeepVerbatim: 30`,
`historyCompactAt: 80`), so the reusable prefix grows monotonically between folds.

At 6,575 average prompt tokens with a system prefix of roughly 1,100 and a
history block carrying most of the rest, a **60-80% prefix hit rate is the
realistic band**. The engineering to earn it is already done. What is missing is a
provider that honors it and reports it.

### What each rung offers

| rung | input $/M | cached input $/M | output $/M | source |
|---|---|---|---|---|
| deepseek-v4-flash | 0.208 (measured) | not reported | 0.251 (measured) | regression over 65,133 rows, r2 0.76 |
| deepseek-v4-pro | 0.432 (measured) | not reported | 1.232 (measured) | 8,622 rows, r2 0.60 |
| cerebras-oss-120b | 0.200 (measured) | none | 0.793 (measured) | 13,056 rows, r2 0.97 |
| mistral-small | 0.106 (measured) | none | 0.300 (measured) | retired rung |
| gemini-flash | 0.276 (measured) | 0.030 | 2.472 (measured) | 336 rows, r2 0.98 |
| glm-5.3-flash | 0.15 list | **0.03** | 0.50 list | Z.ai published |
| qwen36-local | 0 | n/a | 0 | local llama-server |

The measured columns are least-squares fits of `spend = a*prompt + b*completion`
over the window, not list prices. They are what the fleet actually paid.

### Correction on the glm price

The **$0.075/M input is a launch promotion that ends at 24:00 on 2026-09-09
(UTC+8)**, which is 09:00 PDT today. List reverts to $0.15/M input, $0.03/M
cached input, $0.50/M output. Budget against list. The plan below uses list
prices, so the numbers hold after the promo expires.

OpenRouter adds roughly 5.5% on top. Z.ai direct removes it, which is the reason
the `glm-5.3-flash-zai` rung exists, and the reason D1 is worth fixing.

### Verified against the live gateway

Probed `glm-5.3-flash` through LiteLLM: HTTP 200, and the usage payload carries
`prompt_tokens_details.cached_tokens` and `cache_write_tokens`. So the OpenRouter
route both works today and reports cache accounting, with no new credential
needed. That is the path to start on while D1 is being fixed.

**One caveat that must be measured, not assumed.** Reasoning is mandatory on this
model: `reasoning_effort: "none"` is rejected with `Reasoning is mandatory for
this endpoint and cannot be disabled`. On a trivial probe the default effort spent
4 reasoning tokens and `low` spent 15, so it is not catastrophic, but LPP's real
turns are 6.5k-token decisions and reasoning tokens bill as output at $0.50/M
against DeepSeek's measured $0.251/M. The estimates below assume LPP output
inflates 3x from reasoning. That assumption needs a measured canary before the
cutover, not after.

## 4. Per-workload routing table

Baseline column is the post-rotation monthly rate from section 1.

| workload | today | recommended rung | why | $/mo now | $/mo target |
|---|---|---|---|---|---|
| LPP main brain | deepseek-v4-flash | glm-5.3-flash + prefix cache | 99.1% of its tokens are input; prompt is already cache-stable; multimodal so image turns stop failing | 97.28 | 27.72 |
| LPP escalate/actRetry | cerebras-oss-120b | glm-5.3-flash + prefix cache | same prompt shape, Cerebras has no cache at all | 21.41 | 12.60 |
| LPP summarize/fold | mistral-small (retired) | glm-5.3-flash + prefix cache | rung no longer exists in config; folding is input-heavy, output ~34 tok | 18.84 | 8.03 |
| LPP vision leg | lumen-vision | keep | 376 avg input, already noise | 0.08 | 0.08 |
| ops-emergency recurring job | deepseek-v4-pro | glm-5.3-flash | 64/day every day is a timer, not an emergency; identify it and give it its own key | 12.42 | 3.90 |
| ops-emergency true escalation | deepseek-v4-pro | keep | reserve the reasoning rung for actual human-in-loop escalation | (in above) | (in above) |
| lumen-agent (output-heavy) | deepseek-v4-flash | keep | out 1,984 > in 1,743; glm output is 2x DeepSeek's, moving it would cost more | 6.57 | 6.00 |
| lumen-agent (cerebras leg) | cerebras-oss-120b | qwen36-local after D2/D3 | output-heavy local work, no cloud reason | 2.49 | 0.50 |
| lumen-agent (or-free/mistral) | openrouter-free, mistral | consolidate onto deepseek-v4-flash | mistral rung is gone; two paths for one job | 1.45 | 1.00 |
| ops-emergency light leg | deepseek-v4-flash | glm-5.3-flash | 1,388 in / 536 out, no reasoning need | 3.36 | 1.20 |
| minerva classifier | deepseek-v4-flash | glm-5.3-flash + cache | 3,485 in / 152 out is classification; stable system prompt | 2.50 | 0.60 |
| minerva verify judge | gemini-flash | qwen36-local after D2/D3, else glm | 5,937 in / 87 out at $2.47/M output is the worst rate on the board | 0.91 | 0.15 |
| minerva cerebras leg | cerebras-oss-120b | glm-5.3-flash | 1,279 in / 406 out | 0.55 | 0.20 |
| sia-news | deepseek-v4-flash | qwen36-local after D2/D3 | 395 in / 1 out, it returns a single token | 0.33 | 0.00 |
| foreman-local-rung | qwen36-local (leaking to Cerebras) | qwen36-local, fixed | should already be $0; D2 is the whole cost | 0.18 | 0.00 |
| model-watchdog | 4 paid rungs | keep | it is the liveness probe; hitting the paid rungs is the point | 0.66 | 0.66 |
| reflector / bench / misc | various | keep | 0.49 | 0.49 | 0.49 |
| **total** | | | | **169.68** | **63.13** |

### How the LPP target was computed

Monthly LPP volume, scaled from the 14-day post-rotation window:
657.98M input tokens, 5.92M output tokens, 59,421 requests.

At glm-5.3-flash list, output inflated 3x for mandatory reasoning:

| scenario | cached input | uncached input | output | total $/mo |
|---|---|---|---|---|
| no caching | 0 | 657.98M @ 0.15 = 98.70 | 17.8M @ 0.50 = 8.88 | 107.58 |
| 60% hit rate | 394.8M @ 0.03 = 11.84 | 263.2M @ 0.15 = 39.48 | 8.88 | 60.20 |
| **75% hit rate (target)** | 493.5M @ 0.03 = 14.80 | 164.5M @ 0.15 = 24.67 | 8.88 | **48.35** |

Moving rung without caching saves 22%. Moving rung with caching saves 65%.
**Two thirds of the available saving is in the cache, not the rung.** That is the
single most important number in this document.

The LPP rows in the routing table apportion the 75% figure across the three legs
by their share of LPP input tokens (main 59%, summarize 26%, escalate 14%), plus
the vision leg unchanged.

## 5. Projected saving

| case | $/mo | saved vs $169.68 |
|---|---|---|
| do nothing | 169.68 | 0 |
| rung moves only, no caching | 139.60 | 30.08 (18%) |
| rung moves + 60% cache | 92.20 | 77.48 (46%) |
| **full plan, 75% cache** | **63.13** | **106.55 (63%)** |

Annualised, the full plan is roughly $1,280/year against $2,036/year.

Confidence on the LPP half is good: the token volumes are measured, the prices are
published, and the prompt is already built cache-stable. Confidence on the non-LPP
half is lower, because two of its moves depend on qwen36-local becoming reliable
(D2, D3) and one depends on identifying what the 64/day job on the ops-emergency
key actually is.

## 6. Order of work

Nothing here should be done before the one above it.

1. **D6 first.** Register glm pricing in `model_info` on both glm rungs. Without
   it the migration is unmeasurable and the budget governor goes blind. This is
   also the only step that is pure upside with no risk.
2. **D2 and D3.** Break the `qwen36-local <-> cerebras-oss-120b` fallback cycle
   and stop routing multimodal turns at a text-only target. Until this is fixed
   the free rung is a cost source, not a cost sink, and three rows of the routing
   table cannot execute.
3. **Canary the reasoning-token assumption.** Run LPP's real prompt shape against
   glm-5.3-flash over OpenRouter for one session and measure actual
   `completion_tokens` and `cached_tokens`. If output inflates more than 3x, or
   the cache hit rate lands under 50%, the LPP numbers above need redoing before
   any cutover.
4. **Move the LPP main brain** to glm-5.3-flash over OpenRouter, keeping
   deepseek-v4-flash as the fallback. Watch `cached_tokens` in the usage payload
   for one full session. This is 57% of fleet spend in one change.
5. **D1.** Get a valid Z.ai key from Frank via `cord request` and cut the glm
   traffic from OpenRouter to direct, dropping the 5.5% markup. Tracked as
   wq#1587, currently blocked on Frank minting the credential.
6. Move the summarize and escalate legs, then the non-LPP rows.
7. **D4 separately.** The 59% DeepSeek connection failure rate is an availability
   ticket, not a cost ticket, but it does not go away by moving off DeepSeek since
   DeepSeek stays as the fallback.
8. **D7 as a lesson, not a task.** The mistral-small removal moved the fleet's
   biggest workload from a $0.106/M input rung to a $0.208/M one with no cost
   review. A rung retirement that touches a workload above some spend threshold
   should require a price delta in the ticket.

## 7. What this does not cover

- Anthropic access is the Max 20x subscription, not per-token API. It is not in
  these logs and is not part of this plan.
- Request-level latency and quality. The plan assumes glm-5.3-flash is
  quality-adequate for LPP based on its published agentic benchmarks. That is an
  assumption, and step 3 is the place to falsify it.
- The `messages` column is empty on every row, so prompt contents could not be
  inspected from the database. The cache analysis is built from reading
  `prompt.ts` and `config.ts` instead.
- mailbot, entirely — it bypasses the gateway these logs are pulled from. See §8.

## 8. mailbot (wq#2664 — out-of-band spend, not in `LiteLLM_SpendLogs`)

mailbot was absent from this doc until now — `grep -n mailbot` over it returned zero
hits before this section (`MODEL_ROUTING_AUDIT.md` §1 row 20, `CORAL_NEEDLE_TIER.md`
§2.2 have the enumeration story). It makes four `deepseek-v4-flash` calls per inbound
email, all via direct `fetch("https://api.deepseek.com/...")` with a worker-held key —
never through LiteLLM — so none of it shows up in §1's totals, the per-workload table in
§4, or the budget governor's accounting. Everything below is priced from reading
`mailbot/src/worker.js` and `leakfilter.js` (prompt byte counts, `max_tokens` caps),
**grade B**, not measured from spend logs like the rest of this document. Volume is
**grade C**: the only local signal is the D1 mirror `mailbot/mail.db` showing ~15
inbound/day over 2026-09-01→09-03, but `sync_state.last_ts` is 2026-09-03 and
`lumen-mail-sync` is `inactive/dead` (`FLEET_SERVICE_ATLAS.md:294`), so that sample is
three weeks stale — pull live D1 numbers before treating either dollar figure below as
current traffic, not this mirror.

### Two gates already priced, two generative calls that weren't

`CORAL_NEEDLE_TIER.md` §3 priced the two classification gates —
`screenRomanticDrift` (≤1 call/inbound, ~770 worst-case input tokens, `max_tokens:40`,
~$0.00016/call) and `runCritic` (≤2 calls/inbound, ~2065 worst-case input tokens,
`max_tokens:300`, ~$0.00046/call) — at **≤$0.00108/inbound combined**, but flagged the
generative calls as the ones that actually dominate mailbot's DeepSeek bill and left them
unpriced (`CORAL_NEEDLE_TIER.md:203-208`). Same worst-case-per-call convention here (bytes
read from source caps, `deepseek-v4-flash` measured rates from §3 above: $0.208/M input,
$0.251/M output):

| Call | System bytes (worst case) | User bytes (worst case) | Total input tok | Output tok | $/call | Calls per inbound |
|---|---|---|---|---|---|---|
| `generateReply` (`worker.js:176`) | `DEFAULT_PROMPT` 3,089 B (`:52-68`) + `HISTORY_RULE` 433 B + `NOTES_RULE` 387 B + one of `REGEN_ADDENDUM`/critic-note text ≤440 B + canary suffix ≈150 B ≈ **4,500 B** | header + notes (≤500 B) + up to 10 history messages × 700-char cap (`:194`) ≈7,300 B + attachment note + body (3,000 B cap, `sanitizeBody`) + tail ≈ **11,075 B** | (4,500+11,075)/4 ≈ **3,900** | ~400 (110-300 words instructed, `:56`/`:76`; `thinking: disabled` at `:155` so no hidden reasoning burn) | **~$0.00091** | 1-4 — base (`:971`) + optional red-tape regen (`:977`) + up to 2 critic-gate regens (`:1021`) |
| `generateNotesUpdate` (`worker.js:576`) | `NOTES_SYSTEM` 696 B (`leakfilter.js:272`) | prior note ≤500 B + inbound 1,500 B cap + outbound 1,500 B cap ≈ **3,575 B** | (696+3,575)/4 ≈ **1,070** | ~125 (system prompt instructs "Max 500 characters", `leakfilter.js:272`; code backstops at a 600-char slice, `worker.js:599`) | **~$0.00025** | 1 (`:1181`, unconditional, `ctx.waitUntil`) |

Combined per-inbound ceiling (every optional call firing, same convention
`CORAL_NEEDLE_TIER.md` used): $0.00108 (the two gates) + 4×$0.00091 (`generateReply`) +
$0.00025 (`generateNotesUpdate`) = **~$0.005/inbound**. Typical case (1 `generateReply`
call, 1 `runCritic` call, drift screen firing): $0.00016+$0.00046+$0.00091+$0.00025 ≈
**~$0.0018/inbound**.

| Volume | Typical $/mo | Worst-case-ceiling $/mo |
|---|---|---|
| ~15/day (stale D1 mirror — re-pull before trusting) | ~$0.81 | ~$2.27 |
| 100/day (generous, matches `CORAL_NEEDLE_TIER.md`'s own stress case) | ~$5.41 | ~$15.11 |

### Prompt caching on `CRITIC_SYSTEM`: worth doing, not worth ticketing yet

The ticket's specific ask: is `CRITIC_SYSTEM` (2,755 B/call, `worker.js:232-244`) a
caching candidate the way `minerva_classifier`'s system prompt already is (§4 row
"minerva classifier", $2.50/mo → $0.60/mo off the same lever)? **Yes, structurally** —
`CRITIC_SYSTEM`, `DRIFT_SCREEN_SYSTEM` (`:316-321`), and `NOTES_SYSTEM`
(`leakfilter.js:272`) are all sent byte-identical on every call, no per-call
substitution. That is the textbook stable-prefix shape a provider-side or gateway-side
cache is built for.

**`generateReply`'s system prompt is *not* the same shape, and that's worth recording so
nobody "fixes" it the same way by mistake.** `buildSystem()` (`worker.js:126-131`)
substitutes `{{GRIEVANCE}}` (13 options) and `{{MODE}}` (8 options) into `DEFAULT_PROMPT`/
`ANGRY_PROMPT` on every call — 104 combinations, chosen by `Math.random()` — and the
first substitution lands at line 58, about a fifth of the way into the prompt. Prefix
caching needs a byte-identical prefix; past that substitution point almost every call
diverges, so only the short fixed opening (`:52-53`) would ever be eligible, which isn't
enough prefix to matter.

**Whether it's already happening is unverified, and that's the actual next step, not a
routing change.** mailbot calls DeepSeek directly rather than through LiteLLM, so D1's
gateway-level finding ("no row anywhere carries `prompt_cache_hit_tokens`") doesn't cover
it either way — `worker.js`'s `callDeepSeek`/`runCritic`/`screenRomanticDrift` all log
`finish_reason` and content length (`:167`) but never read `data.usage` for a cache-hit
field, so there is zero fleet evidence either way for mailbot specifically. The concrete,
cheap step: log `data?.usage?.prompt_cache_hit_tokens` (or whatever field DeepSeek's
response actually carries) alongside the existing `finish_reason` log lines next time
this file is touched, and read back a week of Worker logs before deciding whether a
caching change is worth making.

**Not filing a ticket for the instrumentation or a caching change.** At $0.81-$15.11/mo
typical-to-ceiling, this sits in the same bracket `CORAL_NEEDLE_TIER.md` §3 already ruled
too small to justify machinery beyond a config change — smaller in typical case than the
$2.50/mo minerva_classifier bill that did get a caching fix, and even the worst-case
ceiling at generous volume is under 10% of the fleet's $169.68/mo baseline (§5). Recorded
here so the next person pricing mailbot starts from evidence instead of re-deriving it,
same reasoning `CORAL_NEEDLE_TIER.md` §2.1 used for `article_ingest.py`.
