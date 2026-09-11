# Prompt/context caching across the rungs — what each provider actually does

wq#1712. Measured 2026-09-11 against the live gateway, not read off vendor docs.
Probes and raw payloads: `ops/wq1712-prompt-cache-survey/`.

FLEET_COST_PLAN.md §3 called the cache "the only large lever left, and it is not
working yet" (§8.5). Both halves of that are wrong. Caching is working, on three
of four rungs, and has been the whole time — what varies between rungs is not
*whether* they cache but *whether the cache survives appending one turn*, which
is the only access pattern our biggest workload has.

## 1. The measurement that decides everything

Every rung caches a byte-identical repeat. That is the easy case and it tells you
nothing, because Lumen never sends the same prompt twice. LPP appends one history
line per turn; a worker's transcript grows every turn. The question is what
happens to the cache when the prompt grows by one line at the end.

Fired at each rung: an LPP-shaped prompt built from the production
`pokemon/src/prompt.ts` (same `SYSTEM_PREFIX`, same append-only history block,
same fixed-order volatile tail), padded to ~6.9k tokens to match LPP's measured
size. Four calls: cold, identical repeat at 2s, identical repeat at 15s, then one
with a single history line appended.
(`ops/wq1712-prompt-cache-survey/deepseek_cache_canary.ts`)

| rung | identical repeat | **+1 appended turn** | verdict |
|---|---|---|---|
| deepseek-v4-flash | 6,784/6,933 = **97.9%** | 6,528/6,964 = **93.7%** | true incremental prefix cache |
| glm-5.3-flash-zai | 6,848/6,905 = **99.2%** | 768/6,936 = **11.1%** | collapses to a fixed 768-token block |
| cerebras-oss-120b | 6,912/6,968 = **99.2%** | 0/6,999 = **0%** | whole-prompt exact match only |
| claude (Claude Code OAuth) | CLI-managed | 89–99% in-session | already optimal, not billed in dollars |

Raw: `cache_canary_deepseek-v4-flash.cold.json`, `cache_canary_glm-5.3-flash-zai.json`,
`cache_canary_cerebras-oss-120b.json`.

**DeepSeek is the only rung whose cache survives our access pattern.** The 436
missed tokens on its append test are not the appended line (~31 tokens) — they
are the appended line *plus the whole volatile tail after it* (notepad, state
block, instruction footer). That is structural and irreducible given
`prompt.ts:73-76`'s ordering, and it is already the right ordering: static system
prefix, then append-only history, then volatile tail last. There is no prompt
rewrite available that improves on 93.7%.

### Two prior findings this corrects

**"Z.ai does not cache" (wq#1592, wq#1616, ISSUE-LOG #274) was a prompt-size
artifact.** Those canaries fired 2,004-token prompts and got zero. At 6,905
tokens the same route returns 6,848 cached tokens. wq#1592's own conclusion
flagged this as the untested possibility — "zero-hit-at-2k-tokens does not rule
out a size threshold" — and it was right. ISSUE-LOG #274 should close: the cache
exists, it just does not help us, for the different reason in the table above.

**"Cerebras has no cache at all" (FLEET_COST_PLAN §3) is wrong.** It caches, at
99.2% on an exact repeat. It is still useless to us, because 0% survives an
append.

## 2. What a cache hit costs

`/model/info` is admin-scoped and the master-key file no longer authenticates
(§5), so the registered rates were recovered from live billing instead. Each call
returns LiteLLM's computed cost in the `x-litellm-response-cost` header — the
same number that lands in `LiteLLM_SpendLogs.spend` — which gives one equation
`cost = h·hit + m·miss + o·out` per call. Four calls with deliberately dissimilar
hit/miss/output mixes over-determine the three unknowns.
(`ops/wq1712-prompt-cache-survey/solve_rates.ts`)

| rung | cache-hit input $/M | cache-miss input $/M | output $/M |
|---|---|---|---|
| deepseek-v4-flash | **0.014** | 0.44 | 1.32 |
| glm-5.3-flash-zai | 0.03 | 0.15 | 0.50 |
| cerebras-oss-120b | **0.00** | 0.35 | 0.75 |

Residuals are zero to nine decimal places on all twelve rows
(`solved_rates_*.json`). The method validates against a known answer: the glm
row reproduces exactly the Z.ai list prices FLEET_COST_PLAN §8.4 registered by
hand the day before (1.5e-07 / 5.0e-07 / 3.0e-08).

Two corrections fall out.

**The ticket's premise figure is off by 4.7x.** DeepSeek cache hits bill at
$0.014/M, not $0.003/M. The saving is still large — a hit is 31x cheaper than a
miss — but the estimate below uses the measured number.

**Cerebras cache reads are registered at $0.00/M, which is an accounting hole,
not a discount.** Cerebras publishes no cache-read discount; its cache is a
latency feature. So every exact-repeat Cerebras call is recorded by the gateway
as costing nothing for its input while the provider presumably still bills for
it. This is FLEET_COST_PLAN's D6 in reverse — D6 was a rung priced at zero and
therefore invisible; this is a *price component* at zero. The budget governor and
`paid_spend_tripwire.py` under-count Cerebras by exactly the cached fraction.
Low-volume today, but it is a blind spot that grows with any Cerebras traffic
that repeats.

### Effective input price, for the pattern we actually send

| rung | effective input $/M at its measured append-hit rate |
|---|---|
| deepseek-v4-flash | 0.937·0.014 + 0.063·0.44 = **0.041** |
| glm-5.3-flash-zai | 0.111·0.03 + 0.889·0.15 = **0.137** |
| cerebras-oss-120b | 0·0 + 1·0.35 = **0.350** |

Nominal list makes glm look 2.9x cheaper than DeepSeek on input ($0.15 vs
$0.44). Once the cache is priced in, **DeepSeek is 3.3x cheaper than glm.** The
rung comparison inverts.

## 3. How much of our traffic is cacheable

89% of fleet billed tokens are input; for LPP it is 99.1% (FLEET_COST_PLAN §1).
Cacheability splits cleanly by workload shape:

| workload | shape | cacheable prefix | on a caching rung today? |
|---|---|---|---|
| LPP brain (4 legs) | stable system prefix + append-only history + volatile tail | ~94% | **no** — moved to glm-5.3-flash-zai |
| minerva classifier | stable system prompt + short varying item | system prefix only, ~3.5k in / 152 out | yes (deepseek-v4-flash) |
| lumen-agent | output-heavy (out > in) | low value, input is the small side | yes |
| ops-emergency light leg | 1,388 in / 536 out | moderate | yes |
| model-watchdog | byte-identical probes | ~100%, but 6–85 tokens | n/a, noise |
| Claude Code workers | growing transcript | 89–99%, CLI-managed | yes, automatically |

The concentration is the same as the spend concentration: LPP is 81% of metered
spend and it is the one workload with a near-perfect cacheable shape, currently
routed to the one paid rung that cannot cache it incrementally.

### Anthropic is not part of the $/mo number

Claude Code runs on the OAuth subscription surface (`models.yaml` `pools:
session`, five-hour rolling quota), so its tokens cost quota, not dollars, and it
contributes $0 to the $169.68/mo metered baseline. Its caching is also already
near-optimal and not ours to tune: manual `cache_control` breakpoints are not
reachable from the `-p` surface (PREFIX_STABILITY.md §premise), in-session hit
rate is 89–99% (TOKEN_BASELINE.md:48), and cross-session turn-1 reuse is a
per-model constant of 16,236 tokens on opus-5 / 24,649 on sonnet-5, identical
across 21 sessions (CWD_STABILITY.md:19-20). The residual lever — the ~10.4k
tokens per spawn that never hit — buys rate-limit headroom, not money, and the
one rule that protects it is already in force: never edit CLAUDE.md, settings, or
the MCP toolset mid-session.

## 4. The change, and what it is worth

**Move LPP's four brain legs back from `glm-5.3-flash-zai` to
`deepseek-v4-flash`.** This reverses the cutover recorded in FLEET_COST_PLAN §8.3
(`lumen-pokemon-live.service` on `lpp-glm.env`, `LPP_BRAIN_{PRIMARY,ESCALATE,
ACTRETRY,SUMMARIZE}=glm-5.3-flash-zai`). It is an EnvironmentFile edit plus a
unit restart — no code change, and `prompt.ts` needs no work because its ordering
is already optimal.

The cutover was made on the belief that glm was cheaper per token. On list price
it is. On the price we actually pay for the prompts we actually send, it is 3.3x
more expensive, because it is the rung that throws the cache away.

At the measured live shape (59,421 req/mo, 4,635 avg prompt = 275.4M input/mo,
FLEET_COST_PLAN §8.3):

| | input | output | $/mo |
|---|---|---|---|
| glm-5.3-flash-zai today (11% append-cache, 432 out) | 275.4M @ 0.137 = 37.65 | 25.7M @ 0.50 = 12.85 | **50.50** |
| glm after the wq#1616 reasoning pin (assume 120 out) | 37.65 | 7.1M @ 0.50 = 3.57 | **41.22** |
| deepseek-v4-flash, 93.7% hit (measured), 39 out | 275.4M @ 0.041 = 11.20 | 2.3M @ 1.32 = 3.06 | **14.26** |
| deepseek-v4-flash, 75% hit (conservative) | 275.4M @ 0.121 = 33.19 | 3.06 | **36.25** |

**Saving band $14–36/mo, central estimate ~$27/mo** ($170–430/yr, ~$325/yr
central) — against a current LPP line of $41–50/mo. The spread is entirely the
production hit rate; nothing else in the table moves much. Two mechanisms pull
the live rate below the 93.7% bench figure: history compaction
(`CFG.historyCompactAt: 80`) rewrites the history block and fully breaks the
prefix once every 80 turns (~1.3 points), and every session restart pays one cold
miss. Neither is large, which is why the measured end of the band is the more
likely one — but it is a bench number until a live session confirms it, and the
conservative end is what should be budgeted against.

DeepSeek's output rate ($1.32/M) is 2.6x glm's, which would matter for an
output-heavy workload. LPP is not one: 39 output tokens per turn against 4,635
input. It matters so little that the move survives even the pessimistic hit rate.

### What not to do

FLEET_COST_PLAN §4 recommends moving the minerva classifier, the ops-emergency
light leg, and the minerva cerebras leg onto glm. **Do not.** The first two are
already on DeepSeek and already caching; moving them to glm trades a $0.014/M
hit for a $0.15/M miss. The plan made those recommendations from list prices at a
time when everyone believed DeepSeek's cache was dead.

### Verification, one session

After the cutover, one LPP session settles the band. `prompt_cache_hit_tokens` /
`prompt_cache_miss_tokens` come back in the usage payload on every DeepSeek call
(proven in every row of `cache_canary_deepseek-v4-flash.cold.json`), so the live
hit rate needs no new instrumentation — only that something reads the field.
Today nothing does: the field is in the payload and absent from every dashboard,
which is the entire reason FLEET_COST_PLAN §D5 concluded "no prompt caching is
active anywhere" from a `cache_hit` column that means something else.

**`LiteLLM_SpendLogs.cache_hit` is LiteLLM's own response-cache flag, not a
provider prefix-cache flag.** It is null on every paid row because we do not run
LiteLLM's response cache. It was never evidence about DeepSeek, and reading it as
such is what sent the fleet's largest workload to a non-caching rung.

## 5. What this could not measure, and why

`/model/info` and `/spend/logs` both return **401** with the key in
`/home/lumen/.secrets/litellm-master.env`, run as the full `lumen` user via
`lumen-run` (`ops/wq1712-prompt-cache-survey/spend_accounting_probe.json`). The
file parses fine — `LITELLM_MASTER_KEY`, 41 characters — so the key is stale
against the running proxy, not malformed.

Two consequences. First, this survey's rates come from the response header rather
than the registry; they are what the gateway bills, which is the number that
matters, but the registry's own `cache_read_input_token_cost` fields were never
read directly. Second, and more urgent: **`ops/wq1406-paid-fallback-guard/paid_spend_tripwire.py`
reads that same key file (line 126) for that same endpoint.** If the key is dead,
the paid-spend alarm has been failing silently. That is a live monitoring gap and
wants its own ticket. Logged as ISSUE-LOG #322.

Also unreachable from this sandbox: the `claude-api` skill (the `Skill` tool
returns `Execute skill: claude-api`, and the plugin directory is outside the
worker's `--add-dir` set) and `WebSearch`/`WebFetch` (not granted). So no vendor
documentation was consulted for any price or capability in this document —
everything here is recovered from live billing, which is why the ticket's
$0.003/M premise could be corrected rather than inherited. Logged as
ISSUE-LOG #321.

The gateway-registered rates are not the same thing as the DeepSeek invoice.
`lumen-deepseek-spend.service` snapshots the real account balance every 15
minutes into `/home/lumen/messenger/` (also outside this sandbox); reconciling
those two series would close the last gap between "what we record" and "what we
are charged", and would catch a D6-shaped drift in either direction. Worth a
follow-up ticket; it does not change the direction of the recommendation here,
only its precision.
