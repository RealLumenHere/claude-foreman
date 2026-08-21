# foreman-tok T4 — post-diet token baseline re-measurement (wq#704)

Depends on TOKEN_BASELINE.md (wq#629), PREFIX_STABILITY.md (wq#630), INSTRUCTION_DIET.md
(wq#631). Those shipped a prompt diet (~5-8% off two live prompts by tiktoken proxy) plus a
static-block-first/volatile-last reorder, but never measured the effect against real Claude
token consumption. This re-baselines it. Zero new Anthropic spend: reads existing session
transcripts only.

## Data-source correction (why this isn't the file the ticket named)

The ticket's `Do` list points at `/home/lumen/foreman/_runs/*.jsonl` and
`/home/lumen/foreman/research/TOKEN_BASELINE_POSTDIET.md`. A prior orchestrator addendum on
this ticket (after 3 killed attempts) corrected that: `/home/lumen/foreman/_runs/` holds only
the original 8-file wq#629 baseline, and said post-diet transcripts live at
`~/.claude/projects/-home-lumen-worktrees-wq*-a*/*.jsonl`.

That path is itself wrong for a build worker: `guard.sh` denies it outright (`BLOCKED —
denied path: /home/lumen/.claude/projects`, confirmed against both the bare directory and a
specific known filename from this ticket's own prior-attempt transcript). That is almost
certainly what killed the previous 3 attempts — there's no way to route around a guard-hook
deny, only to stop asking it the same question.

The real, accessible, real data source turned out to be **`/home/lumen/agents/work-queue/_runs/`**
— one of this session's two allowed working directories, not something that needed
discovery/search (the addendum's "don't spend turns searching for other `_runs` dirs"
warning doesn't apply; this wasn't found by searching, it was already in scope). It holds
real `claude -p --output-format stream-json` transcripts for actual claude-foreman tickets,
dispatched via the interim `FOREMAN_DISPATCH.prompt`/`BUILD_PLAN.md` pattern that
INSTRUCTION_DIET.md confirms is "still live" and was dieted in the same wq#631 session as
`harness/prompts/worker.md`. It has both pre- and post-diet sessions, verifiably classified
(next section), which is exactly the instrument this ticket needs. fworker.py's own
`worker.md`-driven sessions (this ticket's own dispatch mechanism) write their full
transcript to the guard-denied `~/.claude/projects/` path and only a small `attempt-N.json`
state summary to `/home/lumen/foreman/_runs/wq{id}/` — not enough usage detail for this
measurement, and inaccessible for the full stream either way.

## Method

`token_baseline_postdiet.py` (this directory), same method as `token_baseline.py`: parse
`usage` blocks (`input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` /
`output_tokens`) out of real stream-json transcripts, zero new spend. Re-run: `python3
token_baseline_postdiet.py > token_baseline_postdiet.json`.

**Classification**, not by file mtime (unreliable — file mtime is session-end time, and
timestamps alone don't prove which prompt version was actually sent): each `fbuild<N>.prompt`
sidecar is the literal prompt text sent for that session. Pre-diet wording still reads "that
is your spec" / "Do not re-derive"; post-diet (wq#631, landed 2026-08-16T02:39:56Z per commit
`e17a2da`) reads "Don't re-derive". Verified this lines up with mtime for every file it could
check (all pre-diet files predate the commit, all post-diet files postdate it) — the wording
check is used as the classifier because it's what actually determines the prompt an attempt
received, not just a proxy for it.

**Excluded from the headline delta** (11 of 14 real fbuild sessions found):
- 3 files (`wq#627`, `wq#661`, `wq#662`) run on `claude-sonnet-4-6`, not `claude-sonnet-5` —
  downstream of `wq#618` ("T4: local rung, LiteLLM fallback"), which itself is in the clean
  post-diet set below. A different model is a different tokenizer/routing path; folding it in
  would confound the wording-diet measurement with an unrelated infra change. Reported
  separately in `token_baseline_postdiet.json` (`excluded_model_confound`), not analyzed here
  — that's its own ticket's concern.

## Per-ticket rollup — pre-diet (FOREMAN_DISPATCH.prompt, sonnet-5, before wq#631 landed)

| ticket | title | sessions | turns | total tokens consumed | cache hit rate | tokens/turn | cost |
|---|---|---|---|---|---|---|---|
| wq#625 | T2a-BUILD: worker harness per approved design | 1 | 107 | 107,755 | 98.32% | 1,007.1 | $2.39 |
| wq#626 | MC Agents view: live worker cards + transcript feed | 2 | 258 | 332,683 | 98.19% | 1,289.5 | $6.77 |
| wq#639 | fworker shakedown bugs | 1 | 182 | 237,152 | 98.75% | 1,303.0 | $6.05 |
| wq#645 | fworker bug 3: dropped --max-turns | 1 | 183 | 172,945 | 98.51% | 945.1 | $3.96 |
| wq#646 | reliability-S1: verdict engine + shim tests 1-8 | 1 | 177 | 247,229 | 98.79% | 1,396.8 | $6.23 |
| **combined** | | **6** | **907** | **1,097,764** | **98.54%** | **1,210.3** | **$25.41** |

## Per-ticket rollup — post-diet (FOREMAN_DISPATCH.prompt, sonnet-5, after wq#631 landed)

| ticket | title | sessions | turns | total tokens consumed | cache hit rate | tokens/turn | cost |
|---|---|---|---|---|---|---|---|
| wq#616 | T2b: concurrency hardening | 1 | 300 | 414,573 | 99.09% | 1,381.9 | $13.55 |
| wq#618 | T4: local rung (LiteLLM fallback) | 1 | 441 | 432,663 | 99.39% | 981.1 | $19.00 |
| wq#647 | reliability-S2: watchdogs + lease reaper + shim 9-13 | 1 | 345 | 415,405 | 99.29% | 1,204.1 | $15.85 |
| wq#648 | reliability-S3: governor cache-on-429 + shim 14-16 | 1 | 250 | 322,698 | 98.96% | 1,290.8 | $9.47 |
| wq#649 | reliability: fworker bug 4, worktree/branch lifecycle | 1 | 227 | 250,656 | 99.02% | 1,104.2 | $7.61 |
| **combined** | | **5** | **1,563** | **1,835,995** | **99.21%** | **1,174.7** | **$65.48** |

Raw ticket-total-tokens isn't comparable pre vs post (different tickets, different scope —
post-diet total is bigger only because those 5 tickets ran more total turns, 1,563 vs 907,
not because per-turn cost rose). The normalized metrics below are the real comparison.

## Delta (normalized metrics — this is the actual answer to "did the diet work")

| metric | pre-diet | post-diet | delta |
|---|---|---|---|
| tokens/turn | 1,210.3 | 1,174.7 | **-2.94%** |
| cache hit rate | 98.54% | 99.21% | **+0.67pp** |
| avg fixed-prefix overhead (first-turn cache-write) | 8,990.3 | 8,773.2 | **-2.41%** |
| thrash turns (>5k cache-read drop turn-over-turn) | 0 / 907 turns | 0 / 1,563 turns | no change |
| `compact_boundary` frames | 0 | 0 | no change |

**Verdict: real, measurable, small improvement — not the 5-10% (or more) INSTRUCTION_DIET.md
flagged as the open question.** ~3% tokens/turn is directionally consistent with the diet
working, but far short of even INSTRUCTION_DIET.md's own honest ~5-10% figure, because that
figure was measured against the *dieted prompt files themselves* (965→909 tokens,
491→467 tokens — a majority-safety-content prompt with a genuinely compressible ~5-10%
remainder). Real per-turn context also includes the full accumulated transcript, tool
results, and CLAUDE.md, which the diet never touched — so the connective-prose savings are
real but get diluted by everything else that makes up a turn's actual token count. This
isn't a regression or a failure of the diet; it's the expected shape of "we shortened one
component of a much larger whole," reported as measured rather than inflated to match the
source research's headline number (INSTRUCTION_DIET.md set that same expectation already).

## Comparison against the wq#629 baseline (as literally named in the ticket)

wq#629's own baseline totals (from TOKEN_BASELINE.md, cited not re-derived — these 3 tickets
closed before the diet shipped and can't be re-run for a like-for-like post-diet pair):

| ticket | tokens/turn | cache hit rate |
|---|---|---|
| wq#613 | 1,797 | 97.6% |
| wq#614 | 1,437 | 96.4% |
| wq#625 | 1,004 | 99.1% |

These three are a different, smaller, earlier sample (T0/T1/T2a-era tickets, Aug 15 morning)
with high ticket-to-ticket variance (1,004-1,797 tokens/turn) driven by ticket type, not
prompt wording — variance of that size dwarfs a ~3% diet effect, which is exactly why this
ticket's pre_diet_rollup bucket above (also `FOREMAN_DISPATCH.prompt`/sonnet-5, same
`_runs` source and instrument, captured the same week) is the real apples-to-apples control,
not the wq#629 numbers directly. Both are in `token_baseline_postdiet.json` for the record.

## Follow-up items (thrash / regression check, per acceptance criteria)

None found. Zero thrash turns and zero `compact_boundary` frames in both buckets — same as
PREFIX_STABILITY.md's T0 finding, still holding post-diet at these ticket sizes.

**One unexplained, non-regressive observation worth a note for a future ticket:** every file
in *both* buckets (pre- and post-diet alike) shows an identical first-turn
`cache_read_input_tokens` of exactly 24,649 tokens — meaning some prefix is being served from
cache on literally the first turn of every session, contradicting PREFIX_STABILITY.md's T0
finding of zero cross-session cache reuse. Since it's identical in both buckets it isn't
something this ticket's diet caused or fixed either way, and diagnosing the actual caching
mechanism (extended TTL? a warm pre-spawn template session? something else in how the interim
dispatcher spawns children?) needs infra-side information this ticket doesn't have — flagged
as a candidate follow-up, not investigated further here.

## What this baseline does NOT cover

Same caveat TOKEN_BASELINE.md logged and this ticket's own addendum re-confirmed: fworker.py's
own `worker.md`-driven sessions (the harness this repo actually ships, including this very
ticket's dispatch) write their full transcript to `~/.claude/projects/*/<session_id>.jsonl`,
which is guard-denied to a build worker. This baseline instead measures the interim
`FOREMAN_DISPATCH.prompt` dispatcher, which INSTRUCTION_DIET.md confirms received the same
wording diet in the same session and remains the fleet's actual majority dispatch path for
regular tickets today (11 of the 14 real fbuild sessions found here used it). A true
`worker.md`-only re-measurement needs either a guard-hook exception for a specific
operator-run analysis session (not a build worker), or `fworker.py` writing its own
usage-summary sidecar into `RUNS_ROOT/wq{id}/` at exit so this kind of measurement doesn't
need the raw transcript at all — worth its own follow-up ticket if that granularity matters
more than what this file already answers.
