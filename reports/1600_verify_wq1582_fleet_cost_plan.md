# wq#1600 — verify-pass: wq#1582 fleet LLM cost-optimization plan (35d spend data)

**Verdict: CONTRADICTION** — the document's structural claims (spend totals, key-alias
breakdown, D1/D4/D6 defects) check out and are independently corroborated. But the single
number the whole savings case is built on — "a 60-80% prefix hit rate is the realistic
band" for glm-5.3-flash, framed in the doc itself as "the single most important number in
this document" — is not just unverified, it has since been *empirically contradicted* by
the fleet's own follow-up canary, and `FLEET_COST_PLAN.md` as landed has not been updated
to reflect that.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/392 (merged as `19de6fe`, already
on `origin/main`). Diff adds only `research/FLEET_COST_PLAN.md` (305 lines, doc-only).

## What the diff does

Adds `research/FLEET_COST_PLAN.md`: a fleet-wide LLM cost analysis from 35d of
`LiteLLM_SpendLogs`, identifying 7 defects (D1-D7), proposing a per-workload routing table
built around moving LPP (81% of forward run-rate) to `glm-5.3-flash` with prompt caching,
and projecting $169.68/mo -> $63.13/mo (63% saving).

## Verification performed

### 1. Arithmetic, read cold, no DB access assumed

- 35d totals table (`FLEET_COST_PLAN.md:20-27`): the 8 rung-spend rows sum to exactly
  $107.31, matching the stated total. DeepSeek subtotal 64.70+15.38+0.18=$80.26, 80.26/
  107.31=74.8%, matching the doc's own correction ("75% rather than 85%").
- Key-alias table (`:35-42`): 8 rows sum to exactly $107.31.
- Forward run-rate table (`:57-68`): the 12 `$/mo` values sum to exactly $169.68, matching
  the stated run-rate total, and each row's `$/mo` is `$/14d * 30/14` to the cent.
- Section 4 routing table (`:208-224`) does **not** fully reconcile: recomputing the
  `$/mo now` column row-by-row gives $169.52, not the stated $169.68 — a $0.16 gap. Traced
  it to the "everything else" $2.10/mo bucket from section 1 (`:27`) being partially, not
  fully, re-distributed across the new rows added in section 4 (LPP vision 0.08 + minerva
  cerebras 0.55 + sia-news 0.33 + foreman-local-rung 0.18 + reflector/misc 0.49 = 1.63,
  plus +0.31 unexplained on the lumen-agent or-free/mistral row = 1.94 of the 2.10 bucket
  accounted for, $0.16 left over). Also `:224`'s "reflector / bench / misc" row has a
  data-alignment bug: the `why` column contains `0.49` instead of prose, one column short.
  Cosmetic — doesn't change any conclusion — but shows the section-4 allocation wasn't a
  clean re-query, it was hand-split.

### 2. Cross-checked defects against independently-filed follow-up tickets

Each defect that spawned its own ticket confirms the plan's numbers from a second source,
not just the ticket text:

- **D1** (Z.ai 401, `:94-100`) — wq#1587's own independent probe of
  `https://api.z.ai/api/paas/v4/chat/completions` reproduces the identical `HTTP 401
  {"code":"1000","message":"Authentication Failed"}`. Matches.
- **D4** (deepseek-v4-flash 59.2% failure, `:116-119`) — wq#1590: 94,339/159,474 =
  59.15%, cause `Cannot connect to host api.deepseek.com:443`. Matches.
- **D6** (glm-5.3-flash absent from price map, `:126-132`) — wq#1591 fixed this by
  registering `model_info` for both glm rungs. Note: wq#1591 merged (`e85169d`,
  05:07:50 UTC) *before* wq#1582 merged (`19de6fe`, 05:08:27 UTC) — D6 was already
  resolved by the time this PR landed, though the doc still describes it as an open
  defect. This is a fleet-scheduling race between two automated workers, not a factual
  error in the doc (D6 was true when written); noted, not counted against the verdict.
- **Rotation claim** (`:44-47`, wq#949) — wq#949 ("rotate all 14 litellm service keys...")
  is a real ticket matching the description of the pre-rotation key generation.

### 3. The contradiction: D5 / prompt-caching, the load-bearing number

Section 3 (`:139-200`) argues prompt caching is "where most of the money is" and states
(`:156-159`): "a 60-80% prefix hit rate is the realistic band. The engineering to earn it
is already done. What is missing is a provider that honors it and reports it." Section 4's
routing table and section 5's projection are built on a **75% hit rate target**
(`:238,255`), and the doc is explicit about how much of the case rides on it (`:240-242`):
"Moving rung without caching saves 22%. Moving rung with caching saves 65%. **Two thirds
of the available saving is in the cache, not the rung. That is the single most important
number in this document.**"

The doc does hedge this in one place: order-of-work step 3 (`:276-280`) calls for a canary
before any cutover, and sets an explicit redo-trigger: "If output inflates more than 3x,
**or the cache hit rate lands under 50%**, the LPP numbers above need redoing before any
cutover."

That canary was run — `wq#1592`, merged as `571f14e` (05:14:46 UTC, after wq#1582's
`19de6fe` at 05:08:27), documented in `research/GLM53_FLASH_CACHE_CANARY.md`. Three
back-to-back calls against glm-5.3-flash over OpenRouter with a **byte-identical
prompt prefix** (0s / 2s / 10s apart — the best case for a prefix cache to fire):

| call | gap | prompt_tokens | cached_tokens | cache_write_tokens |
|---|---|---|---|---|
| call1_cold | — | 2004 | 0 | 0 |
| call2_repeat_prefix | 2s | 2004 | 0 | 0 |
| call3_repeat_prefix | 10s | 2004 | 0 | 0 |

**0% cache hit rate, including on an identical-prefix repeat.** That is under the plan's
own 50% redo-trigger. By the plan's own stated criteria, the LPP numbers in sections 4-5
need redoing before any cutover — and as of `HEAD` (`571f14e`), `research/
FLEET_COST_PLAN.md` has not been touched since it landed. The $63.13/mo target and the
"single most important number" framing still stand in the document, unrevised and
uncaveated at the point a reader would actually see them (the routing table and the
projection table), even though the one real measurement taken against that number came
back negative.

**Caveat on my own finding**: wq#1592's canary ran at 2,004 prompt tokens against a
synthetic 30-line history; the fleet's measured LPP average is 6,575 tokens
(`FLEET_COST_PLAN.md:74,156`). wq#1592's own writeup flags this and recommends a second
canary at realistic size before treating the result as final. So this is not proof caching
categorically won't work at production prompt size — it is the only empirical data point
gathered so far, it is negative, and it directly contradicts the "60-80% realistic band"
framing that section 3 presents as close to settled rather than as the untested hypothesis
it actually is.

### 4. Capability gaps hit during verification

No live DB or gateway-config access was available to independently re-run the SQL behind
section 1's tables (`docker exec`/`psql` are not on `run_readonly_cmds` in
`harness/fleet-exec-policy.yaml`, and this ticket's `ssh(anubis)` capability grant doesn't
reach the litellm/postgres host regardless — anubis is a generic peripheral node, not the
gateway or DB host, matching the precedent already logged as `kb/ISSUE-LOG.md#256`). Did
not re-log this; same root cause as #256, no new information. Verification of section 1's
raw numbers is therefore limited to internal arithmetic consistency (which holds) and
cross-ticket corroboration (which holds for D1/D4/D6), not independent re-query.

## Recommendation

Confirm the plan's factual/defect claims (D1, D4, D6, rotation history, arithmetic in
sections 1-2) — they hold up. Contradict the headline savings claim in sections 4-5: the
$63.13/mo / 63% figure rests on a caching assumption that the fleet's own canary (wq#1592)
already falsified at the tested scale, and the plan's own redo-trigger for that scenario
has fired without the numbers being redone. Before any LPP cutover decision is made off
this document, either (a) re-run the cache canary at realistic (~6.5k token) prompt size
per wq#1592's own recommendation, or (b) revise sections 4-5 to the "rung moves only, no
caching" scenario ($139.60/mo, 18% saving) as the currently-supported number.
