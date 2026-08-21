# CWD_STABILITY — the worktree-cwd divergence ceiling, measured (wq#705)

Follows PREFIX_STABILITY.md (wq#630) §1b and TOKEN_BASELINE.md (wq#629). Same zero-new-spend
method: reads existing session transcripts, makes no API calls.

Instrument: `cwd_stability.py` in this directory. Re-run with `python3 cwd_stability.py`
(human-readable) or `--json` (diffable). Corpus is the fworker-era transcripts the CLI writes
to `~/.claude/projects/<cwd-slug>/<session_id>.jsonl` — 21 worker sessions, 9 tickets,
2026-08-15 → 2026-08-20, all CLI v2.1.233. That project-dir name *is* the cwd, url-slugged, so
the corpus is self-labelling: one directory per worker worktree. This is the follow-up corpus
TOKEN_BASELINE.md's "What this baseline does NOT cover" asked the next foreman-tok ticket to
pull, and the first foreman data that post-dates INSTRUCTION_DIET (wq#631).

## Premise correction, up front

PREFIX_STABILITY.md §1b predicted that per-ticket worktree cwd makes cross-session cache hits
impossible, and told the next ticket to "expect: still ~0" for first-turn `cache_read`.

**Measured: first-turn `cache_read_input_tokens` is not ~0. It is a per-model constant —
16,236 (opus-5), 24,649 (sonnet-5) — identical across all 21 sessions, 9 tickets, 21 distinct
worktree cwds, 21 distinct branch names, and 4 separate days.** 67.7% of every worker spawn's
turn-1 input is already served from a prefix that per-ticket cwd demonstrably does not disturb.

The ceiling §1b describes is real, but it sits somewhere else and it is much cheaper than
assumed. §1b's *ordering* claim is correct (cwd is the first divergence inside the system
block); its *cost* claim is wrong by roughly an order of magnitude, because the shared region
terminates at a structural breakpoint that already sits before cwd, and the static run between
cwd and the next unavoidable divergence is only ~1,029 tokens.

---

## (a) Measured baseline

### Per-session, first turn

`cw_t1` = `cache_creation_input_tokens` on turn 1 (everything from the first divergence
onward, paid fresh by every spawn). `cr_t1` = `cache_read_input_tokens` on turn 1 (the shared
prefix). On turn 1 a session has no cache chain of its own, so `cr_t1` can only come from a
prefix that was already resident. `prompt` = the dispatched worker prompt, tokenized with
`cl100k_base` as a proxy (same limitation INSTRUCTION_DIET.md logged — Claude's own tokenizer
is not exposed to this box's OAuth CLI surface; valid for relative comparison).

| started | wq | a | model | prompt | cw_t1 | cr_t1 | cw−prompt |
|---|---|---|---|---|---|---|---|
| 2026-08-15T16:59:54 | 603 | 1 | sonnet-5 | 634 | 10,991 | 24,649 | 10,357 |
| 2026-08-15T17:01:57 | 603 | 2 | sonnet-5 | 765 | 11,197 | 24,649 | 10,432 |
| 2026-08-15T17:21:47 | 626 | 1 | sonnet-5 | 582 | 10,964 | 24,649 | 10,382 |
| 2026-08-15T17:23:25 | 626 | 2 | sonnet-5 | 713 | 11,179 | 24,649 | 10,466 |
| 2026-08-15T18:05:11 | 626 | 3 | sonnet-5 | 718 | 11,187 | 24,649 | 10,469 |
| 2026-08-15T19:30:08 | 650 | 1 | sonnet-5 | 585 | 11,033 | 24,649 | 10,448 |
| 2026-08-15T19:30:46 | 650 | 2 | sonnet-5 | 721 | 11,241 | 24,649 | 10,520 |
| 2026-08-16T02:41:49 | 650 | 2 | sonnet-5 | 721 | 11,237 | 24,649 | 10,516 |
| 2026-08-16T02:44:18 | 650 | 3 | sonnet-5 | 719 | 11,224 | 24,649 | 10,505 |
| 2026-08-16T02:48:04 | 650 | 4 | sonnet-5 | 808 | 11,398 | 24,649 | 10,590 |
| 2026-08-19T09:02:46 | 688 | 1 | sonnet-5 | 671 | 10,451 | 24,649 | 9,780 |
| 2026-08-19T09:02:50 | 690 | 1 | sonnet-5 | 645 | 10,411 | 24,649 | 9,766 |
| 2026-08-19T09:07:41 | 690 | 3 | sonnet-5 | 774 | 10,605 | 24,649 | 9,831 |
| 2026-08-19T09:07:58 | 688 | 2 | sonnet-5 | 831 | 10,712 | 24,649 | 9,881 |
| 2026-08-19T09:37:10 | 693 | 1 | sonnet-5 | 911 | 10,822 | 24,649 | 9,911 |
| 2026-08-20T18:56:32 | 705 | 1 | **opus-5** | 676 | 10,070 | **16,236** | 9,394 |
| 2026-08-20T18:56:33 | 704 | 1 | sonnet-5 | 669 | 12,853 | 24,649 | 12,184 |
| 2026-08-20T18:56:33 | 707 | 1 | **opus-5** | 629 | 9,922 | **16,236** | 9,293 |
| 2026-08-20T18:58:47 | 707 | 2 | **opus-5** | 756 | 10,109 | **16,236** | 9,353 |
| 2026-08-20T19:01:15 | 704 | 2 | sonnet-5 | 805 | 13,067 | 24,649 | 12,262 |
| 2026-08-20T19:07:52 | 705 | 2 | **opus-5** | 807 | 10,282 | **16,236** | 9,475 |

The ticket asked for a pair of worker sessions on *different* tickets in *different* worktrees
started close enough in time that TTL isn't the killer. The corpus contains a clean one:
**wq#705-a1, wq#704-a1 and wq#707-a1 all started within 1 second of each other** (18:56:32–33,
three different tickets, three different worktrees, three different branches). All three got
the full per-model `cr_t1` constant. So did wq#688-a1 and wq#690-a1, 4 seconds apart on
2026-08-19. TTL was never the variable being tested here, and cwd divergence did not suppress
the shared prefix in any of them.

### Aggregate

| metric | value |
|---|---|
| sessions / tickets | 21 / 9 |
| attempts per ticket | **2.33** |
| turn-1 shared prefix (`cr_t1`, summed) | 483,977 tok |
| turn-1 fresh write (`cw_t1`, summed) | 230,955 tok |
| **turn-1 input already reused** | **67.7%** |
| mean turn-1 fresh write per session | 10,998 tok |
| all-session consumed (`in + cache_write + out`) | 1,191,623 tok |
| turn-1 fresh write as share of consumed | 19.4% |
| burn on first attempts | 601,238 tok |
| **burn on retries (attempt ≥ 2)** | **590,385 tok (49.5%)** |

`turn1_cache_read_is_cwd_invariant: True` is asserted directly by the instrument (it checks
that each model's `cr_t1` set has exactly one element).

**Snapshot caveat.** The corpus is live, and it includes the wq#705-a2 session that wrote this
document. Whole-session figures (`all-session consumed`, `burn on retries`, and the two shares
derived from them) therefore drift upward on every re-run while a worker is in flight — a
re-run minutes later already read 1,219,918 consumed / 50.7% retry share. The table above is
the snapshot taken at authoring time. The load-bearing numbers are unaffected: everything in
the "first turn" section is measured from turn 1 only and is frozen once a session starts.

### The exact divergence point

Anthropic composes the cache prefix in the order **tools → system → messages**. Within the
system block, the layout is directly observable from a live worker session's own prompt (this
document was written by one, running in `/home/lumen/worktrees/wq705-a2`):

| # | region | tokens | varies per ticket? |
|---|---|---|---|
| 1 | static head — agent identity, `# Harness`, tone/pronoun rules, `# Session-specific guidance`, `# Memory` | ~970 | no |
| 2 | `# Environment` — **primary working directory**, additional working directories, is-a-git-repo | 55 | **YES — divergence #1 (cwd)** |
| 3 | platform / shell / OS / model roster, `# Context management`, `# Delivering work`, `# Corrections`, parallel-tool-call line | **1,029** | no |
| 4 | `gitStatus` — current branch, main branch, git user, status, recent commits | 176 | **YES — divergence #2 (branch + commits)** |

Then, in `messages`: the CLAUDE.md system-reminder (`/home/lumen/CLAUDE.md`, 310 tok), the
worker prompt itself (**divergence #3**, 582–911 tok), and after it the static blocks the CLI
appends — deferred-tools reminder, agent-type list, and the skills catalogue (**4,925 tok** of
frontmatter across the 62 installed `SKILL.md` files under `~/.claude/skills` +
`~/.claude/plugins`; the injected catalogue is a subset of these, so 4,925 is an upper bound).

So the ticket's question — "is it truly at cwd, or does something else diverge first?" — has a
precise answer: **inside the system block, cwd is genuinely first.** Nothing per-session
precedes it. But that fact turns out not to be load-bearing, for two independent reasons:

1. **The shared region already terminates before cwd.** `cr_t1` is a clean per-model constant
   that no worktree, branch, ticket, prompt, deferred-tool count (26 on 08-15/16 vs 15 on
   08-19/20 — `cr_t1` did not move), or calendar day perturbs. Whatever the CLI marks as the
   last shared breakpoint sits ahead of region 2 and is already being reused at full value.
2. **cwd is not the only divergence in its block, and it is not the last one.** Stabilizing
   cwd would extend a matching prefix only as far as `gitStatus`, 1,029 tokens later — and
   `gitStatus` carries the branch name, which is `{type}/wq{ticket}-…-a{attempt}` by
   construction. It cannot be stabilized without abandoning per-ticket branches, at which point
   there is no per-ticket work left to isolate.

**Therefore the maximum span that stabilizing cwd could ever convert from cache-write to
cache-read is region 3: 1,029 tokens per session** — 9.4% of the mean 10,998-token per-session
fresh write, 1.8% of total consumed. And that is a ceiling, not an estimate, because it further
requires a `cache_control` breakpoint to land exactly at the region 3 / region 4 boundary.
Breakpoints sit at coarse structural boundaries, `gitStatus` lives inside the same system block,
and FOREMAN_RESEARCH.md's "NOT-APPLICABLE" ledger already established that manual
`cache_control` placement is unreachable from the CLI's OAuth `-p` surface. **The realistic
gain is 0 tokens.**

### One open question, flagged rather than glossed

Transcripts alone cannot distinguish whether `cr_t1` is genuine *cross-session* server-side
reuse of a tools+static-system prefix, or an in-process pre-warm the CLI performs before the
first logged assistant turn. The CLI logs neither the system prompt nor the request body, so
both remain consistent with the data. **It does not change any conclusion in this document**,
because the ≤1,029-token bound above is derived structurally from prompt layout, not from the
mechanism behind `cr_t1`. Recorded so a future reader does not mistake the ambiguity for an
oversight; the settling experiment is in the follow-ups below.

---

## (b) Options

Costed against the corpus above. Max 20x has no per-token dollar cost — the real currency is
the session/weekly limits (TOKEN_BASELINE.md's premise filter), so dollars are quoted as
API-equivalent at `models.yaml` `api_price_mtok` input rates (opus-5 $5, sonnet-5 $2 per MTok)
× 1.25 for cache-write, purely to give the magnitude a familiar scale.

**Best case for every option is the same 1,029 tok/session** — 21,609 tokens across the whole
21-session corpus, ≈ **$0.08 API-equivalent for the entire corpus**, ≈ $0.02/day at the
observed 5.2 sessions/day over 4 active days. Options differ only in what they cost us, never
in what they save.

### (a) Stable relative-path cwd — symlink `/home/lumen/current-worker` → real worktree
- **Pro:** cheap to implement; preserves the real worktree on disk.
- **Con — fatal:** one symlink cannot point at two worktrees. The corpus shows 3 workers
  starting within 1 second (18:56:32–33) and 5 within 5 minutes on 08-19. Concurrency is not
  hypothetical here; it is the shipped T2b design (`harness/concurrency.py`, commit 06d4491).
  Serializing workers to save 1,029 tok/session trades ~5x throughput for ~1.8% of tokens.
- **Also:** the ticket's own note flags this. Confirmed correct.
- **Verdict: reject.**

### (b) Fixed base cwd — all workers `cd /home/lumen/fworker-base`, ticket paths as args
- **Pro:** stabilizes region 2 without a symlink race.
- **Con — fatal:** worktree isolation is what the guard hook's `rm -rf` scoping and the
  worker-prompt rule ("nothing outside it is yours") are defined against. A shared cwd makes
  every bare `git` invocation ambiguous across concurrent workers and re-opens the
  worktree/branch collision class fixed in e30ef49. `gitStatus` would then report the *base*
  repo, so region 4 still diverges per ticket anyway — the ceiling does not move.
- **Verdict: reject.** Highest-risk option for the same zero real gain.

### (c) Prompt-layer cwd elision
- **Pro:** would be free if it existed.
- **Con — not reachable.** The CLI composes the `# Environment` block itself; no `-p` flag
  suppresses it, and the same OAuth-surface limitation that blocks `cache_control` blocks this.
  Even granting elision, `gitStatus` diverges 1,029 tokens later inside the same block.
- **Verdict: reject as not implementable.**

### (d) Accept the ceiling
- **Cost of accepting:** ≤1,029 tok/session, ≤1.8% of total consumed, ≈$0.02/day
  API-equivalent. Zero risk, zero work, no isolation lost.
- **Verdict: recommended.**

---

## (c) Recommendation — ⚠️ NEEDS FRANK APPROVAL BEFORE ANY IMPLEMENTATION

**Accept the ceiling (option d). Change nothing about `fworker.py` or the worktree layout.**

This ticket changed no harness code and no worktree behaviour, per its own instruction. It adds
two files: this document and `cwd_stability.py`.

The measured cost of accepting is ≤1.8% of worker token burn, and realistically 0% — against
giving up either concurrency (option a) or worktree isolation (option b), both of which are
load-bearing shipped design. PREFIX_STABILITY.md §1b called per-ticket worktrees "a deliberate,
load-bearing design choice, not a bug"; the numbers now support keeping it on cost grounds too,
not just on principle. **PREFIX_STABILITY.md §1b's "expect: still ~0" prediction should be
treated as superseded by this document.**

The freeze+reorder wins from wq#630 are *not* structurally capped in the way §1b feared. Turn-1
input is already 67.7% cache-reuse, and the un-reused remainder is dominated by content that
has nothing to do with cwd.

### Where the tokens actually are — three separate follow-up tickets

Not bundled into this one. Each is a distinct lever, sized against the same corpus so they are
directly comparable to the ≤21,609-token cwd lever.

1. **Retry multiplier — 590,385 tok, 27x the cwd lever.** 2.33 attempts per ticket; 49.5% of
   all worker burn is attempt ≥ 2. Every retry re-pays the full ~11k first-turn write plus its
   entire session. wq#650 alone took 4 attempts. This is far and away the largest single lever
   in the data and it is entirely in our control. Suggested ticket: classify the 12 retry
   sessions by `classification` in `_runs/wq*/attempt-*.json` (KILLED/TURNS dominate the ones
   sampled here) and attack the top cause.
2. **Worker skill-surface pruning — 103,425 tok, 4.8x the cwd lever.** The skills catalogue is
   4,925 tok/session, ~45% of the mean per-session fresh write, and it is appended *after* the
   volatile worker prompt, so it can never be shared no matter what we do to cwd. Unlike cwd,
   the *contents* are ours: a build worker has no use for `dataviz`, `keybindings-help`,
   `statusline-setup`, `lpp-pstack`, `bro`, or `teach`. Suggested ticket: scope the worker
   skill set via the worker settings pack and re-measure with `cwd_stability.py`.
3. **The repo-dependent residual — ~2.4k tok/session, unexplained.** Same model, same day:
   foreman-repo sonnet sessions (wq#704) write 12,184–12,262 post-divergence tokens while
   questfree-repo sonnet sessions (wq#688/690/693) write 9,766–9,911. No worktree in either
   set contains a `CLAUDE.md` or `AGENTS.md`, so auto-loaded repo context is ruled out and the
   cause is genuinely unknown. Worth one ticket because 2.4k/session is itself 2.3x the entire
   cwd lever. Suggested method — and the experiment that also settles the §(a) open question
   above: point `ANTHROPIC_BASE_URL` at a local recording proxy (the LiteLLM rung from
   PROBE_RESULTS.md P6 / commit 79cc638 already proves base-url redirection works) and capture
   the verbatim request body from two worker spawns in different repos. That yields the exact
   `system` array, tool array, and breakpoint placement, at zero Anthropic quota. It was
   deliberately not run here: this ticket's acceptance criteria scoped measurement to existing
   transcripts.

---

## Appendix — breakpoint placement, cache TTL, and the pre-warm question

Added by wq#705 attempt 4. Everything above was landed by attempt 2, which was then killed
on the turn ceiling before it could call `wq done` — so the ticket was re-dispatched and
re-measured from scratch. The independent re-measurement **reproduced the body's numbers
and reached the same recommendation**, which is worth recording on its own: two runs, two
corpora snapshots, one answer.

This appendix adds only what the second run settles that the first left open. Instrument:
`cwd_breakpoints.py` (`--json` for a diffable summary), same zero-spend method, corpus
grown to 31 worker sessions / 108 transcripts by the time of the run.

### (i) The cwd lever is exactly 0 tokens, not ≤1,029

§(a) bounded the lever at 1,029 tokens (region 3, between cwd and `gitStatus`) and noted
the bound "further requires a `cache_control` breakpoint to land exactly at the region 3 /
region 4 boundary". That condition is directly measurable: enumerate every distinct
first-turn `cache_read` value the corpus contains, and the reachable breakpoints are
whatever those values cluster on.

Only two values are ever reachable per toolset — end-of-tools (16,236 opus-5 / 24,649
sonnet-5) and end-of-system (21,723 / ~30,355). **No intermediate value occurs anywhere in
108 transcripts.** There is no breakpoint between cwd and `gitStatus`, so region 3 is not
independently cacheable, and stabilizing cwd alone converts nothing at all.

That closes §(a)'s hedge: the realistic gain is not merely "0 in practice", it is **0 by
construction**, and the ≤1,029-token figure should be read as an upper bound that is
provably not attained.

The corollary sharpens option (b)'s rejection. Reaching the system block requires cwd *and*
the `gitStatus` branch name to match, and the branch is `{type}/wq{ticket}-…-a{attempt}` by
construction. Simulating "cwd and branch both stabilized" — i.e. giving up per-ticket
branches too — yields 23 of 31 sessions hitting, worth $0.62 (0.79% of worker spend), $0.85
(1.09%) at a 100% hit rate. So even the version of this that sacrifices wq#649's
per-ticket-branch invariant buys about 1%. Recommendation unchanged, now with the
next-most-aggressive alternative priced as well.

### (ii) The open question is settled: this is genuine cross-session reuse

§(a) flagged that transcripts alone cannot distinguish server-side cross-session reuse from
an in-process CLI pre-warm, and deferred it to a recording-proxy experiment. The corpus
already answers it.

Which tier a session reaches depends on **other sessions' recent history in the same cwd**:
12 of 12 sessions that reached end-of-system had another session active in an identical cwd
within the hour, with **0 counter-examples**; and 68 pairs of worker sessions that started
under an hour apart in *different* worktrees produced **0** end-of-system hits. A pre-warm
internal to one process cannot produce a dependence on a different process's history, and
cannot explain why the dependence is on cwd specifically. Sessions also exist that read the
whole request from cache (`cache_creation = 0`) on turn 1, which no first-turn pre-warm
explains either.

The recording-proxy experiment in follow-up 3 is still worth running for the *layout*
question it also answers, but it is no longer needed for this one.

### (iii) Effective cache TTL is 1 hour, not the 5-minute default

Across CLI-2.1.233 sessions the cache-creation breakdown is 18,327,654 tokens of
`ephemeral_1h_input_tokens` and **zero** `ephemeral_5m_input_tokens`. The decay curve agrees
— same-cwd sessions reaching end-of-system, bucketed by gap since the previous same-cwd
session's last turn:

| gap | reached end-of-system |
|-----|----------------------:|
| < 5 min (incl. concurrent) | 10 / 12 (83%) |
| 5 min – 1 h | 2 / 8 (25%) |
| 1 h – 24 h | 0 / 19 (0%) |
| > 24 h | 0 / 4 (0%) |

The cliff sits exactly at one hour. Two consequences: the reuse window for any future
prefix work is an hour wide rather than five minutes, and §(b)'s costing multiplier should
be **2× base input** (the 1h cache-write rate) rather than the 1.25× used above. That makes
every figure in §(b) ~1.6× larger and changes no conclusion, since the lever is 0.

### (iv) Where the tokens are, priced — corroborates follow-up 1

Whole-corpus cost lines for the 31 worker sessions, at `models.yaml` input rates with the
corrected 2× write multiplier:

| line | tokens | $ | share |
|------|-------:|--:|------:|
| cache_read | 104,572,945 | 31.71 | 40.6% |
| output | 1,372,734 | 24.66 | 31.6% |
| cache_write, later turns | 3,071,857 | 19.79 | 25.4% |
| cache_write, **first turn** | 355,393 | 1.79 | **2.3%** |
| input | 28,380 | 0.12 | 0.1% |
| **total** | | **78.07** | 100% |

The first-turn cache write — the only line *any* prefix-stability work can touch — is 2.3%
of worker spend, and the system block is about half of that. 89.6% of all `cache_write` is
later-turn context growth: the session writing its own accumulating tool results and
assistant turns back into cache. **66% of worker spend (cache_read + later-turn writes)
scales with turn count, not with prefix stability at all.**

That is the same conclusion follow-up 1 reaches from the retry angle, arrived at
independently: this corpus's three most expensive sessions are wq#705's own killed attempts
at 90–94 turns and $8.66–$9.76 each, against a typical sonnet worker under $2. Retries and
turn growth are the same lever seen from two sides, and it is roughly 30× the cwd lever.

### (v) A harness bug this ticket surfaced by existing

Attempt 2 landed this document and was then killed on the turn ceiling before calling
`wq done`. `fworker.py` routes TURNS/KILLED straight to `handle_failure()`, which — unlike
`handle_resumable_failure()` — never calls `check_artifacts_done()`. So an attempt that has
already shipped its deliverable is re-dispatched as if it had produced nothing, which is
why this ticket ran four times and why two independent copies of this research exist. Worth
its own ticket in the reliability lane rather than the token lane; noted here because the
evidence is this ticket's own `_runs` history.
