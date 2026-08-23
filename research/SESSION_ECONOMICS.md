# SESSION_ECONOMICS — the token program priced in the currency the fleet actually pays (wq#734)

CALL_AUDIT.md (wq#706) ranked the fleet's token levers in dollars. The fleet does not pay
dollars. It pays a flat $200/mo Max 20x subscription whose binding constraint is a pair of
rolling usage windows. This file answers whether that substitution changes anything: what the
windows meter, what one percentage point of a window costs in fleet work, and whether the
wq#729/730 priority order survives being re-denominated.

**Verdict, up front: it survives.** Cache reads are not free against the plan limit — Anthropic
documents them as drawing usage at the discounted cached-token rate — so plan usage and dollars
are the same arithmetic with the same coefficients, and every ranking in CALL_AUDIT.md carries
over unchanged. To make that more than an appeal to documentation, the levers below are
re-scored under the adversarial hypothesis too (cache reads cost *nothing* against the limit).
The lever ranking is invariant under all three weightings tested. What the currency change does
alter is the size of the prize and where the program runs out of road, both covered in
§"What the currency change actually changes".

Instrument: `session_economics.py` (this directory), `session_economics.json` for the raw
output. Re-run: `python3 session_economics.py > session_economics.json`. Zero new Anthropic
spend — like wq#704/705/706 it is parsed out of transcripts that already exist. No harness,
prompt, or settings file was touched.

## Headline

| | |
|---|---|
| What the plan meters | two rolling windows, `five_hour` and `seven_day`, each reported as `used_percentage` 0–100, shared across every model and every Claude surface |
| Do cache reads count against it? | **yes**, at the discounted cached-token rate — not free, not full price |
| Which window binds the fleet | the **7-day** one. Across 39 transcripts the 5-hour window never once produced a utilization reading; the 7-day window walked 62% → 80% |
| Measured exchange rate | one point of the 7-day window ≥ **$16.32** of API list price |
| Weekly window, in list-price terms | ≥ **$1,632/week**, ≥ ~$7,000/month against a $200/month subscription |
| One median build session (fbuild647, $15.85) | ≤ **0.97 points** of the weekly window |
| The five-session clean set ($65.48) | ≤ **4.01 points** |
| wq#729 + wq#730 at CALL_AUDIT's 25–30% | ≤ **1.0–1.2 points** back per five sessions |
| Does the lever order change? | **no** — identical under all three metering hypotheses |

Every "≥" and "≤" above is load-bearing and points the same way; §"What the measurement cannot
do" explains why each is a bound rather than an estimate.

## (1) What the limit actually meters

Claims are graded: **A** = quoted from current Anthropic documentation; **B** = measured from
fleet transcripts here; **C** = inference from A and B; **UNKNOWN** = not settleable from
public information or this corpus.

| # | claim | grade | evidence |
|---|---|---|---|
| 1.1 | The plan exposes exactly two rolling windows to the client: `five_hour` (5-hour rolling) and `seven_day` (weekly), each as `used_percentage` 0–100 plus a `resets_at` epoch. | **A** | statusline reference: "`rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage` \| Percentage of the 5-hour or 7-day rate limit consumed, from 0 to 100" |
| 1.2 | Both windows are consumed simultaneously, and a burst can exhaust the weekly one first. | **A** | errors reference: "Usage counts against the session and weekly allowances at the same time. A single burst of heavy activity, such as a large workflow fanout, can exhaust the weekly allowance before the session window resets." |
| 1.3 | Session and weekly windows are shared across all models and all surfaces (claude.ai, Claude Code, Desktop, IDE). Only Opus carries an additional model-scoped weekly limit. | **A** | errors reference: "The session and weekly limits are shared across all models, so switching models doesn't restore access. The Opus limit applies only to Opus requests"; support 11647753: "your usage of all different Claude product surfaces … counts towards the same usage limit" |
| 1.4 | The meter is token-shaped, not request-shaped: it responds to conversation length, model choice and effort level. | **A** | support 11647753: "Your usage is affected by several factors, including the length and complexity of your conversations, the features you use, which Claude model you're chatting with, and the effort level you've selected." |
| 1.5 | **Cache reads count against the limit, at the cached-token rate.** | **A** | costs reference, "Why usage climbs in a long session": "With prompt caching, Claude Code re-reads that history at the cached token rate, so a one-line question in a session that has been open all day still draws usage for the whole conversation." |
| 1.6 | A cache *miss* is a distinct and heavier draw on the limit — heavy enough that `/usage` flags it by name once it accounts for ≥10% of recent usage. | **A** | costs reference: "the `/usage` breakdown flags behaviors that account for 10% or more of your recent usage, such as long context or cache misses" |
| 1.7 | The cached-token rate is ~10% of the standard input rate. | **A** | prompt-caching reference: "`cache_read_input_tokens` \| Tokens served from cache on this turn, billed at roughly 10% of the standard input rate" |
| 1.8 | On a subscription the CLI requests the 1-hour cache TTL automatically. | **A** | prompt-caching reference: "On a Claude subscription, Claude Code requests the one-hour TTL automatically". Corroborates CALL_AUDIT.md's independent finding that `usage.cache_creation` puts 100% of cache creation in `ephemeral_1h_input_tokens`. |
| 1.9 | The plan meter uses the same weighted-token arithmetic as the bill (input 1×, cache write 2×, cache read 0.1×, output 5×, on the rates CALL_AUDIT.md solved). | **C** | 1.4 + 1.5 + 1.7 give the meter the same three terms the dollar formula has — content length, cache state, model/effort — each moving the same direction. Anthropic publishes no coefficients, so this is inference, not documentation. |
| 1.10 | The exact weight of an output token relative to an input token *in the plan meter* (5×, as in dollars, or something else). | **UNKNOWN** | Never published. §"What the measurement cannot do" shows this corpus cannot recover it either. §5 bounds how much it could matter. |
| 1.11 | Whether the `used_percentage` denominator is fixed per plan tier or varies with load. | **UNKNOWN** | Not published. Nothing in the corpus tests it. Treated as fixed throughout. |
| 1.12 | The fleet's account has no overflow valve: extra usage is off. | **B** | Every `five_hour` frame in the corpus carries `overageStatus: "rejected"`, `overageDisabledReason: "out_of_credits"`, `isUsingOverage: false`. A window hitting 100% is a hard stop, not a spend event. |
| 1.13 | The CLI also reports an undocumented `seven_day_overage_included` pool, which read 0.81–0.83 at the same wall-clock minute the plain `seven_day` pool read 0.69–0.70. | **B**, meaning UNKNOWN | Observed only on the one Fable session in the corpus (`fable617`). Not in the statusline reference, and `governor_core.parse_limits` has no counterpart for it. Flagged as a follow-up rather than interpreted. |

**On 1.5, which is the claim this ticket was filed to test.** The worry in the ticket was that
99%-cache-hit sessions might be nearly free against the limit, which would make the dollar
figures a bad guide and demote the wq#729 tool-output cap. Anthropic's documentation says the
opposite in as many words: the re-read *draws usage*, discounted but not free. The correct
mental model is that a long session leaks plan usage continuously just by existing, which is
exactly what CALL_AUDIT.md's carry cost already measures.

## (2) The exchange rate, measured

The CLI emits a `rate_limit_event` frame whenever plan-limit state changes, carrying
`{rateLimitType, utilization, resetsAt}` — the account's own position in the window.
CALL_AUDIT.md read per-call `usage` out of the same transcripts. Nothing before wq#734 joined
the two. Joining them prices fleet work in window points directly.

Across 39 transcripts in `/home/lumen/agents/work-queue/_runs`, the corpus holds 59 such frames:
49 on `seven_day`, 7 on `five_hour`, 3 on `seven_day_overage_included`. Every weekly-pool frame
carries the same `resets_at`, 2026-08-20 11:00 — one window, observed end to end.

### The trajectory

| when | 7-day utilization | reported by |
|---|---|---|
| 2026-08-15 08:05 | 0.62 | `fbuild625r`, `fleet9`, `mv4-drain` (independently, same minute) |
| 2026-08-15 19:27 | 0.70 | `ftok630` |
| 2026-08-16 15:08 | 0.76 | `mcfix`, `fbuild649b`, `fleet12`, `fbuild618`, `gen636` (five sessions, same minute) |
| 2026-08-17 20:15 | 0.78 | `rabbitNOW`, `rabbitSUP` |
| 2026-08-18 14:00 | 0.80 | `rabbitMisty2` |

Over that 77.9-hour span the window advanced **18 net points**. The corpus records **4,240 API
calls** in the same span: 666,717,087 cache-read tokens, 7,110,797 cache-write, 32,442 fresh
input, ~2.54M output — **$293.85** at the list rates CALL_AUDIT.md solved.

    $293.85 / 18 points  =  $16.32 per point of the weekly window (lower bound)

Scaled to the whole window: the weekly allowance is worth **at least $1,632** of API list
price, i.e. at least ~$7,000/month of list-price inference against a $200/month subscription.
That is the real "value multiple" of the plan for this workload, measured rather than
advertised, and it is a floor.

### The same span in the three candidate currencies

| metering hypothesis | per point of the 7-day window | implied weekly window |
|---|---|---|
| raw tokens (everything weighs 1) | ≥ 37.6M tokens | ≥ 3.76B tokens |
| cost-weighted (input 1, write 2, read 0.1, output 5) | ≥ 5.20M input-equivalents | ≥ 520M input-equivalents |
| free cache reads (adversarial) | ≥ 1.50M input-equivalents | ≥ 150M input-equivalents |
| — in list-price dollars | ≥ $16.32 | ≥ $1,632 |

### What a fleet session costs in points

At ≥$16.32/point, the dollar figures already in CALL_AUDIT.md convert directly. These are
upper bounds on points, for the same reason $/point is a lower bound.

| | dollars | ≤ points of the weekly window |
|---|---|---|
| `fbuild647`, the audited median build session | $15.85 | 0.97 |
| `fbuild618`, the largest in the clean set | $19.00 | 1.16 |
| `fbuild616` | $13.55 | 0.83 |
| the five-session clean set | $65.48 | 4.01 |
| wq#729 R2 saving (cap tool results at 1k) across the clean set | $19.10 | 1.17 |
| wq#729 + wq#730 combined, at CALL_AUDIT's realistic 25–30% | $16.37–$19.64 | 1.00–1.20 |

**A median build session costs about one percent of the fleet's week.** With
`floors.json`'s `weekly_all_min_remaining_pct: 15` reserving the last 15 points for Frank, the
dispatchable weekly budget is 85 points ≈ **at least 87 median build sessions**, before any
other lane spends anything. A 25–30% token cut buys 25–30% more of them — roughly 22 to 26
additional build sessions per week.

## (3) Method, and why each number is trustworthy

### Per-call usage is exact on the input side

`input_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens` in the streamed
`message_start` usage are final for that call. Summed per unique `message.id` — CALL_AUDIT.md's
correction, one frame per content block, not per call — they reproduce the `result` frame
exactly. After the per-session rescale described below, reconstructed dollars match every
session's reported `total_cost_usd` to a relative error of 0.0.

### Output tokens are allocated, not measured per call

`output_tokens` in that same frame is **not** final: it is the placeholder the stream opens
with. On `fbuild618` the per-id sum is 1,642 against a billed 119,138. The session total from
the `result` frame is therefore spread uniformly across the session's calls. Output is ~0.2% of
raw tokens and ~10% of dollars, and the intervals measured here mostly contain whole sessions,
so the allocation error lands well inside the noise. It is flagged rather than hidden, and it
is the reason §5's output-weight sensitivity is argued rather than measured.

### Dollars are the CLI's own figure, per session

The corpus is not all sonnet-5 — by dominant model it is 33 sonnet-5 sessions, 4 sonnet-4-6,
1 fable-5 and one with no `modelUsage` block, and those price differently. Rather than maintain a price table, each session's list-price total is
rescaled to the `total_cost_usd` the CLI itself reported. On sonnet-5 sessions the rescale
factor is within 6.6% of 1.0 at worst, which is an independent re-confirmation of CALL_AUDIT.md's
solved `3.00 / 15.00 / 0.30 / 6.00` rates. Note these are *list-price* dollars, computed
client-side; they are the unit CALL_AUDIT.md used, not what Frank is billed.

### The reconstructed clock is cross-validated

The `-p` stream carries no per-line timestamps, so a frame at line *i* of *n* is placed at
`mtime - duration_ms + duration * i/n`. That would be a weak assumption on its own. It is
checked by concurrency: the fleet runs many workers at once, and independent sessions see the
same account-wide utilization. Twelve contiguous runs of one utilization value span more than
one session, with a **median spread of 9.3 minutes** — five separate transcripts independently
place 0.76 at 2026-08-16 15:08. The longest of the twelve is a genuine plateau rather than clock
error — utilization sat at 0.77 for 28.7 hours while a light overnight fleet was offset by
rolling expiry — which is why the median rather than the max is the figure quoted.

### Intervals are measured first-crossing to first-crossing

With several workers live, one crossing gets reported by each of them within seconds, and the
consumption that caused it happened *before* the earliest report. Pairing the last sighting of
*u* with the first sighting of *u+1* would therefore measure a near-empty window. Every
interval below runs from the first sighting of *u* to the first sighting of *u+1*.

### The 5-hour window never registered

Of the 59 frames, 7 name `five_hour` — and every one of them carries `status: "allowed"` with
**no utilization field at all**, while all 49 `seven_day` frames carry a utilization, 26 of them
below the 0.75 warning threshold. Over four days and 39 transcripts the fleet never moved
the 5-hour window far enough for the CLI to report on it, while the weekly window climbed 18
points. Claim 1.2's "a burst can exhaust the weekly allowance before the session window resets"
describes this fleet exactly. **The weekly window is the fleet's binding constraint**; the
governor's `session_min_remaining_pct: 20` floor is guarding the window that never bound.

## (4) The levers, re-ranked in each currency

A chunk of context costs `w_write*tokens + w_read*reads` under weighting *w*; assistant-generated
chunks additionally cost `w_out*tokens` to produce. `call_audit.json` stores tokens and
carry-token-reads for all four categories, and every R-lever's dollar saving, so the read count
behind each lever inverts out exactly. Under `cost_weighted` this reproduces CALL_AUDIT.md's
$15.85 session to $15.92 (0.45%, from approximating the boot payload's read count), which is
the check that this is the same arithmetic and not a new one.

### Where a session's cost sits

| category | raw tokens | cost-weighted (= dollars) | free cache reads |
|---|---|---|---|
| tool results | 52.8% | **46.5%** | 25.4% |
| assistant thinking | 17.8% | 21.9% | **36.0%** |
| assistant tool-call parameters | 12.6% | 16.9% | 31.8% |
| boot payload (system prompt + tool schemas + CLI block) | 16.4% | 14.0% | 5.8% |
| assistant prose | 0.4% | 0.6% | 1.1% |

### The levers

Each percentage is the lever's saving as a share of the whole session in that currency; `#` is
its rank within that column.

| id | lever | dollars | raw | cost-weighted | free cache reads |
|---|---|---|---|---|---|
| **R2** | cap any tool result at 1,000 tokens | $4.72 | 34.1% **#1** | 29.6% **#1** | 14.6% **#1** |
| **R1** | cap any tool result at 2,000 tokens | $3.42 | 24.8% #2 | 21.5% #2 | 10.1% #3 |
| **R3** | bound `Read` — grep/offset before whole-file | $3.41 | 24.7% #3 | 21.4% #3 | 10.2% #2 |
| R4 | cap `Bash` output at 1,000 tokens | $1.47 | 10.6% #4 | 9.2% #4 | 4.5% #4 |
| R5 | suppress re-reading a file already in context | $0.34 | 2.4% #5 | 2.1% #5 | 1.3% #5 |
| R8 | delete every word we wrote (slab + CLAUDE.md + trailer) | $0.17 | 1.3% #6 | 1.1% #6 | 0.2% #7 |
| R6 | keep errored tool results out of the carry | $0.03 | 0.2% #7 | 0.2% #7 | 0.2% #6 |

R1–R4 remain alternative aggressiveness settings on one lever, not additive, exactly as in
CALL_AUDIT.md.

**The order is invariant.** The only movement anywhere in the table is R1 and R3 trading ranks
2 and 3 under the adversarial hypothesis — they are within 0.1 percentage points of each other
there — and R6/R8 swapping at the bottom, where both are rounding error. **wq#729 holds rank 1
and wq#730 holds rank 2-or-3 under every weighting tested, including the one the ticket was
worried about.** R8's recommendation to leave `/home/lumen/CLAUDE.md` alone gets stronger, not
weaker: deleting every word the fleet wrote is worth 0.011 points of the weekly window.

## (5) What the currency change actually changes

Three things, none of them the ranking.

**The prize is a schedule, not a budget line.** In dollars, wq#729+wq#730 saves money the fleet
was never billed for. In points, it converts to roughly 22–26 extra build sessions per weekly
window, or the same throughput finishing sooner inside it. That is the number worth quoting to
Frank, and it is the first defensible one the program has produced.

**A cap on the window is a wall, not a bill.** Claim 1.12: extra usage is off, so hitting 100%
stops dispatch until `resets_at`. Under per-token billing an over-budget fleet slows down;
under this plan it stops. That asymmetry is what makes the governor's reserve floors the right
design, and it makes the 7-day floor the one that matters.

**The program's ceiling rests on claim 1.9, which is inference rather than documentation.** If cache reads count at the
cached rate (documented, claim 1.5), tool results are 46.5% of a session and the levers in §4
address most of what is addressable. If they did not count, assistant generation — thinking
plus tool-call parameters — would be 67.8% of a session, and thinking cannot be disabled on
sonnet-5 (CLAUDE_CLI_INTERNALS.md §F). The program would run out of levers much sooner. This
does not change what to do next, since R2 leads under both, but it is the sensitivity to watch
if wq#729/730 land and the observed point-per-session cost does not fall as predicted. That is
the cheapest available test of claim 1.9, and it costs nothing extra: **wq#729/730 are
themselves the experiment.** Predicted: a 25–30% dollar cut moves a median session from ≤0.97
to ≤0.70 points. If the measured points-per-session barely moves, claim 1.9 is wrong and this
file needs revisiting.

## (6) What the measurement cannot do

**Every per-point figure is a lower bound, and three effects push the same way.**

1. *The 7-day window is rolling.* Usage older than the window ages out continuously, so a net
   advance of 18 points understates gross consumption by whatever expired meanwhile. The
   corpus shows this directly: utilization sat flat at 0.77 for 28.7 hours across an overnight
   lull while the fleet kept working, and the same value recurs days apart.
2. *The corpus is partial.* `_runs/*.jsonl` holds `-p` fleet workers dispatched through
   `FOREMAN_DISPATCH.prompt`. Frank's interactive sessions, claude.ai, Desktop and IDE all draw
   on the same pool (claim 1.3) and are invisible here. Worse, the `fworker.py` shipping path
   writes to the guard-denied `~/.claude/projects/`, so this file carries the same blind spot
   wq#704 and wq#706 logged.
3. *Utilization is quantized to whole points.* One point is the reporting quantum, so any
   single interval carries up to a point of rounding.

**The corpus cannot identify the weighting.** This is the honest limit on §1.9, and it is
structural rather than a sample-size problem. Cache reads are **97.8%–99.3%** of raw tokens in
every usable interval, so the three hypotheses are very nearly collinear: the ratio between raw
and cost-weighted consumption stays inside 6.33–8.31 across all nine intervals. Against
per-interval scatter of cv 0.35–0.53 at n = 9, that spread carries no signal. The `free_cache_read`
weighting happens to have the lowest cv (0.348 against 0.463 and 0.525), and that ordering
should be given no weight whatsoever — it is one noisy sample of nine, and it contradicts a
documented claim. **The weighting question is settled by claim 1.5, not by this data**, and the
data's contribution is the exchange rate, not the coefficients.

To break the collinearity, some traffic has to have a genuinely different mix. Every session in
this corpus is a long-context agentic coding run, and they all look the same.

**Not covered:** whether `used_percentage` denominators differ per plan tier or move under load
(1.11); the `seven_day_overage_included` pool's semantics (1.13); anything about the Opus-scoped
weekly limit, which no session in the corpus touched; and the `worker.md`/`fworker.py` dispatch
path.

## (7) The cheapest probe that would settle it — proposed, not run

This ticket is read-only, so neither of these was executed.

**First, and free: make every future session a data point.** wq#704 and wq#706 both asked for
`fworker.py` to write a usage sidecar into `RUNS_ROOT/wq{id}/` at exit. Add the two
`rate_limits` fields to it — `five_hour.used_percentage` and `seven_day.used_percentage`, plus
`resets_at`, captured at session start and session exit. The CLI already exposes both to the
statusline (claim 1.1) and the governor already polls the same numbers into
`/home/lumen/foreman/state.json` every 60–90s. Nothing new is spent, the guard-denied transcript
path stops mattering for this question, and mix variation accumulates on its own as lanes with
different shapes run. Seeded as **wq#736**.

**Second, if the sidecar's natural variation is still too collinear after a week:** two
controlled `-p` sessions, back to back, during the off-peak window `floors.json` already
defines (05:00–11:00 PT, Mon–Fri), with the `STOP` kill-switch set so no other lane dispatches
and the account is quiet. Session A is deliberately cache-read-heavy and output-light; session
B is output-heavy and short. Read `seven_day.used_percentage` immediately before and after each.
With the fleet quiesced the delta is attributable, and two opposite mixes break the collinearity
this corpus cannot. Cost: ≤2 points of a weekly window, about two ordinary build sessions.
Seeded as **wq#737**, blocked on wq#736 — do not run it until the free path has been given a
week to fail.

## (8) Follow-ups seeded

Filed separately, not implemented here.

- **wq#736** — `fworker.py` usage sidecar, extended with the `rate_limits` fields at session
  start and exit. Free, and it is the prerequisite for everything else in §7.
- **wq#737** — the two-session controlled probe of §7, blocked on wq#736.
- **wq#738** — governor floors are denominated in the wrong window. `session_min_remaining_pct: 20`
  guards a 5-hour window that never registered across 39 transcripts, while
  `weekly_all_min_remaining_pct: 15` guards the one that actually moved 18 points in four days.
  Re-tune with the exchange rate in §2 in hand: 15 points is ~15 median build sessions of
  reserve. **Frank's call** — it is his interactive headroom being sized, so this ticket
  proposes numbers and does not change them.
- **wq#739** — `governor_core.parse_limits` has no branch for the `seven_day_overage_included`
  pool the CLI reported at 0.81–0.83 while `seven_day` read 0.69–0.70 (claim 1.13). With extra
  usage off (claim 1.12) nothing breaks today. Establish what the pool means before that
  changes.

Nothing in this file has been applied to any prompt, settings file, harness, or governor
configuration. wq#734 is the analysis.

## (9) wq#736 landed — new data source for §7's collinearity break

`fworker.py` now writes `RUNS_ROOT/wq{id}/usage-{attempt}.json` at the end of every attempt
(`do_attempt()`, both a clean child exit and every watchdog kill — inactivity, wall-clock,
turns-exceeded, post-result-stuck — since the write sits after `run_child()` returns
regardless of which path got it there). Each sidecar carries `rate_limits_start` and
`rate_limits_exit`: `{five_hour: {used_percentage, resets_at}, seven_day: {used_percentage,
resets_at}}`, read from `/home/lumen/foreman/state.json` (the governor's own poll, `governor/
foreman_usaged.py`) rather than a new API call, plus the result frame's own `usage`/
`total_cost_usd`/`duration_ms` totals for that session. Zero new Anthropic spend, and it
sidesteps the guard-denied `~/.claude/projects` path this file's corpus was limited to (§6,
point 2). As sidecars accumulate across lanes with different token shapes, the per-session
start/exit deltas are the free probe §7 proposed — check back once a week's worth has built up
before spending the two controlled sessions of wq#737.

## (10) wq#737 — controlled-probe attempt, deferred with cause (2026-08-23)

wq#737 is the controlled two-session probe §7 proposed as a last resort, gated on wq#736's
sidecar running for a week and failing to break the collinearity first. Two earlier attempts
(2026-08-22, session `03a11ec1-4998-4e1e-87d5-337555b342d3`) exited in 10 and 3 turns with no
diff and were marked FALSE_COMPLETE — apparently recognizing the ticket as premature and
stopping without leaving evidence. This attempt instead checked the sidecar's actual state,
via the reusable `sidecar_collinearity_check.py` added alongside this section.

**The free path has 6.75 hours in it, not a week.** `RUNS_ROOT/wq{id}/usage-*.json`
(`/home/lumen/foreman/_runs`) holds 135 sessions with matched `rate_limits_start`/
`rate_limits_exit` seven-day readings, spanning 2026-08-23 02:26–09:07. wq#736 merged 2026-08-22
08:22, so an 18-hour gap sits before the first sidecar file — the effective accumulation window
is shorter than "time since merge" suggests.

- **Mix**: cache-read is **98.06%** of the 406.1M raw tokens in the window, and 84 of 87
  sessions with nonzero raw tokens sit in the 90–100% band — the same range the original
  39-transcript corpus showed (97.8–99.3%, §6). The one clean outlier (a 2,116-raw-token
  session, cache-read fraction 0) is four to five orders of magnitude smaller than the rest and
  cannot move a quantized reading.
- **Signal**: `seven_day.used_percentage` moved **29 → 30**, one point, across the whole
  6.75-hour, 406M-token, 135-session window; per-session deltas are 0 for 133/135 records. At
  this rate a week would accumulate on the order of 25 points — the same order of magnitude as
  the original 77.9-hour/18-point trajectory in §2. That is consistent with §7's own estimate
  of how long the free path needs, not evidence the wait was unnecessary.

**Why the controlled probe was not run this attempt — two independent reasons, not one.**
First, timing: only 6.75 of the ~168 hours §7 asks for have elapsed. Second, and unforeseen when
§7 was written: `governor/floors.json` no longer defines a live off-peak window. Commit
`890afe3` (2026-08-23 02:30 UTC, minutes before this attempt started) zeroed every reserve floor
and set `off_peak_days_pt: []` — "floors ZEROED + off-peak disabled per Frank 2026-08-23 (3rd
instruction) ... Do not restore without Frank" (refs wq#715). `state.json` confirms it live:
`off_peak: false`, `stop_active: false`, `weekly_all.percent: 30` and climbing, with sidecar
files from dozens of concurrently dispatching tickets landing seconds apart. The fleet is not
quiesced — it is explicitly, repeatedly instructed by Frank to run at full throughput with no
reserve — so a delta measured now would not be attributable, which defeats the probe's premise
independent of how little time has passed. Restoring the floor is Frank's call per that same
commit note, so re-enabling the mechanism this ticket's design depends on is out of scope here.
Separately, `fworker`'s per-attempt wall-clock cap (3,600s, per `_runs/wq737/attempt-{1,2}.json`)
is shorter than the time to the old 05:00 PT window even if it still applied, so waiting it out
inside one dispatch was never mechanically possible.

**Claim 1.9, restated.** Still grade C — this attempt adds evidence, not a coefficient. The
free path has not had its week and cannot yet be judged to have failed; the interim data is
directionally consistent with §7's prediction, not a counterexample to it. Re-run
`sidecar_collinearity_check.py` before spending wq#737's two-session budget again: it reports
whether the mix has broken free (skip the probe) or whether a week has passed with the mix
still collinear (free path exhausted as designed). Either way, before spending the quota, first
confirm an off-peak/quiescence mechanism is actually live again — as of this writing it is not,
and enabling one is Frank's decision, not this ticket's.

## Sources

Anthropic documentation, retrieved 2026-08-21:

- [Manage costs effectively](https://docs.claude.com/en/docs/claude-code/costs) — claims 1.5, 1.6
- [Claude Code error reference](https://docs.claude.com/en/docs/claude-code/errors) — claims 1.2, 1.3
- [How Claude Code uses prompt caching](https://docs.claude.com/en/docs/claude-code/prompt-caching) — claims 1.7, 1.8
- [Status line configuration](https://docs.claude.com/en/docs/claude-code/statusline) — claim 1.1
- [How do usage and length limits work?](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) — claims 1.3, 1.4
- [Models, usage, and limits in Claude Code](https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code) — background on metering by sign-in method
- [Use Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan) — claim 1.3

Fleet data: `/home/lumen/agents/work-queue/_runs/*.jsonl`, 39 transcripts, 2026-08-15 → 2026-08-19.
Prior work: `CALL_AUDIT.md` (wq#706), `CWD_STABILITY.md` (wq#705), `TOKEN_BASELINE_POSTDIET.md`
(wq#704), `CLAUDE_CLI_INTERNALS.md` §D and §F, `governor/README.md`, `governor/floors.json`.
