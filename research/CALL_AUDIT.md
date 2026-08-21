# foreman-tok T6 — full per-call payload audit (wq#706)

Depends on TOKEN_BASELINE_POSTDIET.md (wq#704) and CWD_STABILITY.md (wq#705), both merged.
Those answered "did the prompt diet move the number" (+2.9% tokens/turn) and "does the cwd
lever move it" (zero). This one answers the question underneath both: **for a real worker
session, what is every byte in every API request, and which of those bytes are ours to
remove.** Zero new Anthropic spend — everything below is parsed out of transcripts that
already exist.

Instrument: `call_audit.py` (this directory). Re-run:
`python3 call_audit.py > call_audit.json`, or `--session <file>.jsonl` for a different one.

## Headline

For the audited session, the bill splits like this. Percentages are of the session's own
reported `total_cost_usd`, and the rows sum to it.

| what | tokens entering context | lifetime cost | share |
|---|---|---|---|
| **(g) tool result payloads** | 156,561 | **$7.41** | **46.8%** |
| (f) assistant thinking | 63,425 | $2.54 | 16.0% |
| (a)+(i) injected system prompt, tool schemas, CLI boot block | 34,511 | $2.00 | 12.6% |
| output generation (not carried) | 125,863 | $1.89 | 11.9% |
| (f) assistant tool-call parameters | 56,012 | $1.85 | 11.7% |
| **(b)+(c)+(e) every word we wrote: prompt slab + CLAUDE.md + ticket trailer** | **1,378** | **$0.086** | **0.5%** |
| (f) assistant prose | 1,930 | $0.07 | 0.4% |
| | | **$15.85** | **100.0%** |

Three things follow immediately.

1. **The prompt is not the problem.** Everything wq#631's diet was allowed to touch —
   the whole slab, CLAUDE.md, the ticket trailer — is 0.5% of a session. Deleting all of
   it, guardrails included, saves 8.6 cents on a $15.85 session. The diet's measured 5.8%
   shave off the slab was therefore worth about a third of a cent. That is consistent with
   wq#704 measuring only -2.9% overall, and it closes that thread: there is no version of
   prompt compression that is worth more than a rounding error.
2. **Tool results are the problem.** Just under half the bill is the output of the
   worker's own tool calls, re-read on every subsequent request.
3. **Position matters as much as size.** A chunk added before call *k* is re-read on every
   call from *k* to *N*. In this 188-call session a token added at call 1 costs
   **$0.062 per 1,000**; the same token added at the midpoint costs $0.034 per 1,000. Early
   exploratory output is the expensive kind.

## Method, and why each number is trustworthy

### Data source

`/home/lumen/agents/work-queue/_runs/*.jsonl`, per wq#704's data-source correction and this
ticket's orchestrator addendum. `/home/lumen/foreman/_runs/` holds only the wq#629 baseline
files, and `~/.claude/projects/` is denied by `guard.sh` — not attempted.

### An "API call" is a message id, not a stream frame

Claude Code emits one `assistant` frame per content block and repeats the `message_start`
usage on each. Summing usage per unique `message.id` reproduces the `result` frame totals
exactly (376 / 289,166 / 40,744,106, asserted in `integrity`); summing per frame does not.
Unique message ids also equal `result.num_turns`.

**This is a correction to wq#704's denominator.** That file counted assistant frames as
turns (345 for this session, against 188 real calls), so its per-turn figures are low by
~1.8x: wq#647's 1,204 "tokens/turn" is 2,210 tokens per API call. The bias is identical in
both of its buckets, so its **-2.9% pre/post delta stands unchanged** — only the absolute
per-turn numbers need rescaling.

### Request-body size is measured, not estimated

For each call, `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` is the
exact size of the request body in the model's own tokens. `growth_k = context_k -
context_(k-1)` is then exactly what the intervening messages added. Growth was non-negative
on all 187 steps, and the trajectory is smooth: 35,889 tokens at call 1 → 110,439 at call 25
→ 240,964 at call 100 → 313,817 at call 188.

### Attribution inside a growth step

Two inputs:

- **Thinking** comes from the `system/thinking_tokens` frames. Their deltas sum to 63,425
  against a billed 62,670 (1.2% error), so this is a measured quantity. Thinking is present
  in every later request: growth never fell below the preceding call's thinking estimate on
  any of the 187 steps. In `-p` there is only one real user turn, so nothing ever clears it.
- **Visible blocks** (text, tool-call parameters, tool results) get a `cl100k_base` proxy,
  rescaled per step so the parts sum to that step's measured growth.

### The cl100k proxy understates real tokens by ~1.8x

Two independent measurements, from disjoint quantities:

- Assistant side: billed non-thinking output (`output_tokens` minus `thinking_tokens`) is
  63,193 against a cl100k count of 34,835 for the same blocks. Ratio **1.81**. The model
  counted these itself, so no invisible wrapper text can explain it.
- Tool-result side: total new input tokens, minus the boot payload, minus thinking, minus
  non-thinking output, is 152,063 against a cl100k count of 93,144. Ratio **1.63**.

Across all five clean-set sessions the ratios are 1.81–1.91 and 1.63–2.04. Two derivations
from unrelated quantities landing in the same band is why this reads as a tokenizer gap
rather than hidden payload.

**Consequence for prior research:** every tiktoken figure in TOKEN_BASELINE.md and
INSTRUCTION_DIET.md understates real billed tokens by roughly this factor on code, shell
output, JSON and paths. Prose is a different mix and probably sits closer to 1.0–1.3x, so
do not apply 1.8x to CLAUDE.md without measuring it. This does not change any conclusion in
those files — both were before/after deltas in a single consistent unit.

### Prices are solved, not looked up

`3.00 / 15.00 / 0.30 / 6.00` per Mtok (input / output / cache read / 1h cache write)
reproduces the reported `total_cost_usd` to a relative error of 0.0 in every clean-set
session. `models.yaml` lists sonnet-5 at the `2/10` intro rate; the CLI billed `3/15`. The
1h cache tier is what's in use — `usage.cache_creation` puts 100% of cache creation in
`ephemeral_1h_input_tokens`. **`models.yaml`'s sonnet-5 `api_price_mtok` is stale and should
be corrected** (seeded as a follow-up below).

## Session selection

The five-session clean set from wq#704 (post-diet, `claude-sonnet-5`, `FOREMAN_DISPATCH.prompt`),
all on CLI 2.1.233 with the same 26 tools and cwd `/home/lumen/foreman`:

| session | API calls | cost | boot payload | first-call cache read | mean call context |
|---|---|---|---|---|---|
| fbuild616 | 151 | $13.55 | 35,705 | 24,649 | 226,727 |
| fbuild618 | 238 | $19.00 | 35,646 | 30,398 | 216,129 |
| **fbuild647** | **188** | **$15.85** | **35,889** | **24,649** | **218,264** |
| fbuild648 | 126 | $9.47 | 35,665 | 30,390 | 180,494 |
| fbuild649b | 129 | $7.61 | 35,706 | 24,649 | 143,025 |

**fbuild647-6d3c8b.jsonl** is the full-audit subject: median on call count (188 of
126–238), median on cost, and closest to the set's mean call context. Not an outlier on any
axis. Every table below is that session unless it says otherwise; the per-category shape
was then checked against the other four (last section) and holds.

## (a)–(e) The boot payload — what carries in call 1 and therefore in all 188

The first request is 35,889 tokens, and it is 16.4% of every input-side token the session
ever pays for. Its composition:

| category | tokens | lifetime cost | note |
|---|---|---|---|
| (b) prompt slab, static block | 898 | $0.056 | `FOREMAN_DISPATCH.prompt` above the `---` |
| (c) `/home/lumen/CLAUDE.md` | 310 | $0.019 | untouched by wq#631, by design |
| (e) ticket trailer, volatile | 170 | $0.011 | ticket id + ORCH addendum |
| (d) settings permission table | 331 | $0.021 **ceiling** | see below |
| (a)+(i) everything else | 34,511 | $2.003 | Anthropic system prompt + 26 tool schemas + CLI session block |

Slab / CLAUDE.md / trailer are cl100k counts, so the real figures are somewhat higher and
(a)+(i) correspondingly lower — by a few hundred tokens, not by anything that changes the
picture. **Our text is at most 3.8% of the boot payload.**

**On (d), the settings pack:** whether the allow/deny table is serialized into the request
at all cannot be settled from a transcript, and the one way to check — reading the CLI
bundle — is outside this worker's allowed directories. What is bounded is its size: the
whole `permissions` object in `harness/worker-settings.json` is 331 cl100k tokens, so **if**
it ships in full it costs $0.021 per session. It is inside the (a)+(i) figure either way, so
it is not double-counted above. There is no version of trimming the deny-list that is worth
more than 2 cents a session, and every entry in it is a house safety rule.

**On the boot payload's stability:** the total is 35,646–35,889 across all five sessions, a
0.7% spread, despite different tickets, different trailers and different git state. What
does vary is how much of it arrives already cached — two clusters, 24,649 and 30,390–30,398.
This refines wq#704's "unexplained observation" of an invariant 24,649 first-turn cache
read: that number is invariant in three of the five files, not all five, and the thing that
is actually invariant is the payload total. The read/write split just reflects which cache
breakpoint a prior session left warm inside the 1h TTL. Since the split moves ~5,750 tokens
between "already cached" and "written this session", any decomposition anchored on
`cache_creation` alone is unstable; the table above is anchored on the total instead.

## (f)+(g)+(h) The rolling transcript — where the money actually is

Everything after call 1. 277,928 tokens accumulate over 187 steps (median step: 675 tokens,
largest: 16,667).

| component | tokens | messages | cache-read token-reads | lifetime cost | share of session |
|---|---|---|---|---|---|
| tool results (g)+(h) | 156,561 | 187 | 21,566,104 | $7.41 | 46.8% |
| assistant thinking | 63,425 | 111 | 7,204,993 | $2.54 | 16.0% |
| tool-call parameters | 56,012 | 187 | 5,057,559 | $1.85 | 11.7% |
| assistant prose | 1,930 | 46 | 179,932 | $0.07 | 0.4% |

Tool results by tool — this is category (h) split out of (g), since `Read` results *are*
the file attachments:

| tool | calls | tokens | mean | lifetime cost | share of session |
|---|---|---|---|---|---|
| **Read** (h) | 33 | 90,968 | 2,757 | **$4.50** | **28.4%** |
| **Bash** | 110 | 62,147 | 565 | **$2.78** | **17.6%** |
| Edit | 37 | 2,978 | 80 | $0.11 | 0.7% |
| Write | 7 | 468 | 67 | $0.01 | 0.1% |

`Edit` and `Write` are already cheap: they return a one-line confirmation, not the file. The
transcript's `tool_use_result` field carries the full old/new strings for an Edit, but that
is a CLI-side record and never reaches the request body — `message.content` is what ships,
and this audit counts only that.

## Top 20 largest single messages

`born` is the call the message first appeared in; `carried` is how many requests it appears
in; cost is its lifetime input-side cost (one cache write plus every later read).

| # | category | tool | tokens | born | carried | cost | what |
|---|---|---|---|---|---|---|---|
| 1 | tool result | Bash | 16,570 | 9 | 180 | **$0.989** | `which wq; find / -maxdepth 4 -iname "wq*" ...` |
| 2 | tool result | Read | 14,470 | 15 | 174 | **$0.838** | `harness/fworker.py`, whole file |
| 3 | thinking | — | 9,350 | 39 | 150 | $0.474 | |
| 4 | tool result | Read | 6,678 | 26 | 163 | $0.365 | `tests/test_fworker_integration.py`, whole file |
| 5 | tool result | Read | 6,643 | 7 | 182 | $0.401 | `BUILD_PLAN.md`, whole file |
| 6 | tool result | Read | 5,402 | 36 | 153 | $0.279 | `tests/test_classifier.py`, whole file |
| 7 | thinking | — | 5,400 | 37 | 152 | $0.277 | |
| 8 | tool result | Read | 5,209 | 51 | 138 | $0.245 | `harness/classifier.py`, whole file (1st of 2) |
| 9 | thinking | — | 5,050 | 63 | 126 | $0.220 | |
| 10 | tool result | Read | 4,696 | 25 | 164 | $0.258 | `tests/fake_claude.py`, whole file |
| 11 | thinking | — | 4,450 | 36 | 153 | $0.230 | |
| 12 | thinking | — | 4,400 | 79 | 110 | $0.170 | |
| 13 | tool result | Read | 4,300 | 43 | 146 | $0.213 | `harness/classifier.py`, whole file (2nd of 2) |
| 14 | tool result | Read | 4,294 | 14 | 175 | $0.250 | `harness/classifier.py` in the source tree |
| 15 | tool result | Bash | 4,203 | 20 | 169 | $0.237 | `grep -n "def cmd_\|reclaim\|..." /home/lumen/bin/wq \| head -100` |
| 16 | tool result | Read | 4,172 | 35 | 154 | $0.216 | `/home/lumen/bin/wq`, offset 688 limit 160 |
| 17 | tool result | Read | 3,600 | 12 | 177 | $0.212 | `research/RELIABILITY_SPEC.md`, whole file |
| 18 | tool result | Bash | 3,527 | 19 | 170 | $0.200 | `grep -n "lease\|claim\|..." /home/lumen/bin/wq \| head -100` |
| 19 | tool result | Bash | 3,215 | 183 | 6 | $0.024 | `tail -10 .../LPP_QUEUE_PROGRESS.md` |
| 20 | tool result | Read | 3,023 | 24 | 165 | $0.167 | `tests/test_shim_matrix.py`, whole file |

Fifteen of the top 20 are tool results; five are thinking. Zero are anything we wrote.

Row 19 is the position effect in one line: 3,215 tokens born at call 183 cost 2.4 cents,
while row 20's 3,023 tokens born at call 24 cost 16.7 cents. Same size, 7x the cost.

## Top 10 tool calls by output size

| # | tool | tokens | born | cost | invocation |
|---|---|---|---|---|---|
| 1 | Bash | 16,570 | 9 | $0.989 | `which wq; find / -maxdepth 4 -iname "wq*" 2>/dev/null \| grep -v proc; ...` |
| 2 | Read | 14,470 | 15 | $0.838 | `/home/lumen/foreman/harness/fworker.py` |
| 3 | Read | 6,678 | 26 | $0.365 | `harness/tests/test_fworker_integration.py` |
| 4 | Read | 6,643 | 7 | $0.401 | `/home/lumen/foreman/BUILD_PLAN.md` |
| 5 | Read | 5,402 | 36 | $0.279 | `harness/tests/test_classifier.py` |
| 6 | Read | 5,209 | 51 | $0.245 | `.../foreman-fix-wq647-watchdog-reaper/harness/classifier.py` |
| 7 | Read | 4,696 | 25 | $0.258 | `harness/tests/fake_claude.py` |
| 8 | Read | 4,300 | 43 | $0.213 | `.../foreman-fix-wq647-watchdog-reaper/harness/classifier.py` (again) |
| 9 | Read | 4,294 | 14 | $0.250 | `/home/lumen/foreman/harness/classifier.py` |
| 10 | Bash | 4,203 | 20 | $0.237 | `grep -n "def cmd_\|reclaim\|expire\|def main\|add_parser" /home/lumen/bin/wq \| head -100` |

Row 1 deserves a note. That single filesystem-discovery command — a worker working out
where `wq` lives — cost 6.2% of the session on its own, and it landed at call 9 so it was
carried 180 times. **This one is already fixed on the shipping path:** commit `cb31db8`
put the full `/home/lumen/bin/wq` path into `worker.md`, so an `fworker.py`-dispatched
worker no longer has to go looking. `FOREMAN_DISPATCH.prompt`, which is what this session
ran on and what still dispatches the majority of fleet tickets, does not carry that fix.

## Ours vs injected

| category | reducible by us? | what it costs |
|---|---|---|
| (a) Anthropic system prompt + tool schemas | **no** | inside the $2.00 boot line |
| (i) CLI session block (env, git state, skills, agents, memory index) | partly, indirectly | inside the same $2.00 |
| (b) prompt slab | yes, and already dieted | $0.056 |
| (c) CLAUDE.md | yes, needs Frank | $0.019 |
| (d) settings permission table | yes, but it's the safety pack | $0.021 ceiling |
| (e) ticket trailer | necessary variable content | $0.011 |
| (f) thinking | **no** — adaptive thinking cannot be disabled on sonnet-5 | $2.54 |
| (f) tool-call parameters | partly — fewer, better-targeted calls | $1.85 |
| (g) tool results | **yes, and this is the whole game** | $7.41 |
| (h) file reads | **yes** — bound the read | $4.50 of the $7.41 |

On (f) thinking: `CLAUDE_CLI_INTERNALS.md` §F records that adaptive thinking is always-on
for fable-5 / sonnet-5 / opus-4.7+ and cannot be disabled; `MAX_THINKING_TOKENS` only bites
on the 4.6-era pair. So $2.54 (16.0%) is a floor for as long as workers run on sonnet-5.
Effort level is the only lever that plausibly touches it, and that is a quality trade, not
a token trade — out of scope here.

## Reduction proposals

Savings below are simulated across all five clean-set sessions (**$65.48 total, 832 API
calls**) by removing the tokens at the call they actually entered context and recomputing
the carry. Each row states the measured baseline it targets.

| # | proposal | measured baseline | est. saving (5 sessions) | needs Frank? |
|---|---|---|---|---|
| R1 | Cap any single tool result at ~2,000 tokens | 20 results over 2k in fbuild647 alone | **$13.69 (20.9%)** | no |
| R2 | Cap at ~1,000 tokens instead | 40 results over 1k in fbuild647 | **$19.10 (29.2%)** | no |
| R3 | Bound `Read` — grep/offset before whole-file | 61 of 152 reads were whole-file, 22.1% of set cost | **up to $14.46 (22.1%)** | no |
| R4 | Cap `Bash` output at ~1,000 tokens | 16 Bash results over 1k in fbuild647 | $4.36 (6.7%) | no |
| R5 | Suppress re-reading a file already in context | 10 repeated invocations in fbuild647 | $0.63 (1.0%) | no |
| R6 | Keep errored tool results out of the carry | 7 errored results in fbuild647 | $0.57 (0.9%) | no |
| R7 | Port the full-path-`wq` fix into `FOREMAN_DISPATCH.prompt` | one discovery command, $0.99 | ~$1 per affected session | no |
| R8 | Shorten `/home/lumen/CLAUDE.md` | 310 cl100k tokens | **$0.37 (0.56%)** for deleting *all* our prompt text | **yes, and not worth asking** |

R1–R4 overlap: they are alternative aggressiveness settings on the same lever, not additive.
The realistic combined target is **R1 or R2, plus R3** — call it 25–30% of fleet build spend.

**R1/R2/R4 — cap tool output.** Two candidate mechanisms, and picking between them is a
spike, not a decision this audit can make from transcripts: (a) a prompt-level rule telling
workers to pipe through `head`/`tail` and to prefer `grep -n` over `cat`; (b) a `PreToolUse`
hook that rewrites the invocation before it runs. Mechanism (b) depends on whether CLI
2.1.233 honours an `updatedInput` from a `PreToolUse` hook, which needs a read of the CLI
bundle — outside this worker's allowed directories, so unverified and deliberately not
asserted. `guard.sh` does no output capping today, and neither live prompt carries any
output-discipline rule. A prompt-level rule is additive text in the connective-prose region
of both prompts and touches nothing on INSTRUCTION_DIET.md §Scope's preserved-verbatim list
(safety rules, deny rationale, provenance, no-human-present) — but it does mean *adding*
~50 tokens to a prompt to save ~25% of a session, which is the correct trade by three orders
of magnitude.

**R3 — bound the read.** The strongest single lever and the least invasive: 61 of 152 reads
across the set pulled a whole file. The $14.46 figure is the full carry cost of those reads
and is therefore an upper bound; the worker genuinely needed *some* of those bytes. Treat it
as "the pool this lever draws from", not as a promise.

**R5/R6 — hygiene.** Small but essentially free, and R5 has a correctness benefit beyond
tokens: two copies of the same file at different revisions in one context is a known way to
confuse an editing agent.

**R8 — CLAUDE.md.** INSTRUCTION_DIET.md deliberately scoped this out and recommended a
separate Frank-approved ticket if it were ever wanted. **This audit's recommendation is to
close that thread rather than open it.** CLAUDE.md costs 1.9 cents a session. It is
fleet-wide, outside any git repo, and carries the busy-key coordination protocol every
concurrent agent on the box depends on. Editing it risks that protocol to save two cents.
Not proposed, and flagged here only so the open item from wq#631 can be retired with a
measurement behind it.

Nothing in R1–R7 touches a preserved-verbatim block. No recommendation here has been applied
to any prompt, settings file, or CLAUDE.md — this ticket is the audit.

## Does the shape hold across the clean set?

| session | calls | cost | tool results | thinking | tool params | boot payload | output |
|---|---|---|---|---|---|---|---|
| fbuild616 | 151 | $13.55 | $6.65 (49.1%) | $1.84 (13.6%) | $1.70 (12.6%) | $1.68 (12.4%) | $1.54 |
| fbuild618 | 238 | $19.00 | $9.78 (51.5%) | $2.71 (14.3%) | $2.01 (10.6%) | $2.58 (13.6%) | $1.79 |
| fbuild647 | 188 | $15.85 | $7.41 (46.8%) | $2.54 (16.0%) | $1.85 (11.7%) | $2.09 (13.2%) | $1.89 |
| fbuild648 | 126 | $9.47 | $4.42 (46.7%) | $1.41 (14.9%) | $0.92 (9.7%) | $1.38 (14.6%) | $1.30 |
| fbuild649b | 129 | $7.61 | $3.01 (39.5%) | $1.09 (14.3%) | $0.85 (11.2%) | $1.44 (19.0%) | $1.05 |

Tool results are the largest line in all five (39.5–51.5%). Thinking holds a tight 13.6–16.0%.
Our own prompt text is 0.50–0.77% in all five. fbuild647 sits mid-range on every column,
which is what the selection criteria were for.

## Follow-up tickets seeded

Filed separately per the acceptance criteria, not implemented here:

- **wq#729** — tool-output discipline lever, spike then implement (R1, R2, R4, R7). The
  `PreToolUse` `updatedInput` question is the blocking spike; it decides hook-vs-prompt.
- **wq#730** — bound file reads, suppress repeat reads, drop errored results from the carry
  (R3, R5, R6). Shares wq#729's mechanism question.
- **wq#731** — correct `models.yaml`'s stale sonnet-5 `api_price_mtok` (2/10 → 3/15) and
  record the cache-tier multipliers, so the governor prices sessions correctly.
- **wq#732** — rescale wq#704's per-turn figures to per-API-call, note the ~1.8x
  cl100k-to-real tokenizer factor in TOKEN_BASELINE.md / INSTRUCTION_DIET.md, and refine its
  24,649 observation. Deltas in those files are unaffected; only absolute figures change.

R8 is deliberately not seeded. Its recommendation is to leave CLAUDE.md alone, which needs
no ticket.

## What this audit does not cover

- **The `worker.md`/`fworker.py` path.** Same limitation wq#704 logged: `fworker.py`
  sessions write their transcript to the guard-denied `~/.claude/projects/`, so the audited
  sessions are `FOREMAN_DISPATCH.prompt` dispatches. The two prompts are close in size (467
  vs 909 cl100k tokens post-diet) and the categories that dominate — tool results, thinking,
  boot payload — are properties of the CLI and the work, not of which prompt started it. The
  clean fix is `fworker.py` writing a usage sidecar into `RUNS_ROOT/wq{id}/` at exit, which
  wq#704 already flagged; it is worth doing and would make this audit re-runnable on the
  shipping path.
- **Inside the (a)+(i) boot line.** The split between Anthropic's system prompt, the 26 tool
  schemas, and the CLI's session block (env, git state, skills list, agent list, memory
  index) cannot be resolved from a transcript. It is 12.6% of a session and almost none of
  it is ours, so the value of resolving it further is low.
- **`/home/lumen/CLAUDE.md` as it stood in August.** The 310-token figure is today's file.
  It has no git history to check against, and at 1.9 cents a session the difference does not
  matter.
- **Whether the settings permission table ships in the request at all.** Bounded at 331
  tokens either way; see (d) above.
