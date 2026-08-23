# foreman-tok — worker output discipline (wq#632)

Depends on the wq#629 T0 baseline. Premise (unchanged, from wq#629): we run Max 20x with
no API, so tokens matter because session/weekly *limits* are token-driven, not because of
per-token dollars. Output tokens are the interesting class because they become input on
every later turn of the same session.

Instrument: `output_discipline.py` in this directory. Zero new Anthropic spend — parses
existing files only. Re-run: `python3 output_discipline.py > output_discipline.json`.

**Headline: the premise holds, the proposed lever does not.** Output really is 28% of
consumed tokens (not the 0.2–3.3% the wq#629 baseline reported — see the denominator note
below). But only 1.7–2.7% of output is prose, and the brevity rules this ticket specifies
reach about **0.2% of output tokens**, because fleet workers were already doing almost all
of it. The rules landed anyway; the honest reason is regression insurance, not savings.

On the gate: the delta is measured here from real transcripts — reachable savings, prefix
cost, and net, all below. What is *not* achievable is observing that delta after
deployment. The between-session noise floor measures at 15.7% on the very metric the rules
move, roughly 15x the effect size. That is demonstrated with a control split, not asserted.

## Ponytail: NOT VERIFIED, not adopted

The ticket says to verify the "Ponytail" YAGNI/minimalist behavioral skill (claimed ~54%
generated-volume / ~20% session reduction) exists and check its license *before* adopting
anything, and otherwise to implement the rules natively in `worker.md`.

Verification was not possible from a build worker: `WebSearch` and `WebFetch` both come
back `has not been granted`, and `~/.claude/skills` is outside this session's allowed
working directories. No local copy exists anywhere in scope. So the claim is **unverified
— neither confirmed nor refuted**, and nothing was adopted from it. That is the branch the
ticket already records ("otherwise implement the rules natively"), so this is not a blocked
Frank decision. Anyone wanting to revisit it needs a session with network egress; note the
measurement below implies the ceiling for this class of change on our fleet is far below
the claimed 54%, whatever the skill turns out to be.

## Two sources, because neither one is sufficient alone

Source A is a live directory: the fleet keeps running, so re-running the instrument moves
these numbers. Every Source A figure below is the snapshot in `output_discipline.json` as
committed (88 sessions). Source B is a fixed historical set and does not drift.

**Source A — `/home/lumen/foreman/_runs/wq*/usage-N.json` (88 sessions).** fworker's own
per-attempt usage sidecars. These *are* worker.md-driven sessions — the exact population
this change applies to — and they carry authoritative totals including a real (not
estimated) `output_tokens_details.thinking_tokens`. They carry no content, so they can say
how much output there was but not what it was.

**Source B — `/home/lumen/agents/work-queue/_runs/fbuild*.jsonl` (the wq#704 clean set,
5 sessions / 832 API calls).** Full stream-json transcripts, so the content is there and
can be decomposed. Not worker.md sessions (they ran under `FOREMAN_DISPATCH.prompt`), but
the same class of build work by the same model, and the only observable content on this box:
worker.md sessions write their transcripts to `~/.claude/projects/`, which `guard.sh` denies
outright. That denial is what killed three attempts of wq#704 and, per its prior-attempt
notes, likely this ticket's earlier attempts too — it cannot be routed around.

The two sources agree on the one quantity both can measure — thinking share of output,
**51.9% (A, authoritative) vs 51.1% (B, estimated)** — which is the cross-validation that
makes B's content decomposition trustworthy for A's population.

### Source A coverage caveat

136 `usage-N.json` files exist; 88 are usable. The other 48 have no usage totals at all
because the session died before emitting a result frame — 36 `LIMIT`, 6 `CRASH`, 3 `HANG`,
3 `FALSE_COMPLETE`. Those sessions burned real tokens that this baseline does not count, so
every per-ticket figure below is an **underestimate**, and the `LIMIT` bucket is
systematically excluded — which is unfortunate given limits are the whole reason this
programme exists. Making fworker checkpoint usage on abnormal exit is a follow-up worth
filing.

Relatedly, `attempts_per_closed_ticket` reads 1.0 only because no ticket in this window has
both a usage-bearing failed attempt and a usage-bearing success. It is not evidence that
retries are free.

## T0 — output-token baseline (Source A, 88 sessions, 5,275 API calls)

| metric | all sessions | SUCCESS only (64) |
|---|---|---|
| output tokens | 2,276,954 | 2,007,406 |
| output tokens / API call | 431.7 | 433.8 |
| of which thinking | 51.9% | 51.4% |
| non-thinking output / API call | 207.9 | 211.0 |
| output share of consumed¹ | 28.4% | 29.0% |
| output tokens / closed ticket | — | 31,366 |
| consumed tokens / closed ticket | — | 108,048 |

¹ "consumed" = `input + cache_creation + output`, the wq#629 house definition, which
excludes `cache_read`. This is why output reads as 28% here and 0.2–3.3% in
TOKEN_BASELINE.md — that report divided by a denominator that *included* cache-read, which
grows with every turn of a session and swamps everything else. Both numbers are correct
about different things; 28% is the one relevant to a limit that counts consumed tokens.

**These are the numbers a post-change re-measurement diffs against.** Re-running
`output_discipline.py` after N post-change tickets close regenerates them in place.

### By model — Opus workers emit 2x the non-thinking output

| model | sessions | API calls | output/call | non-thinking output/call | thinking share |
|---|---|---|---|---|---|
| claude-sonnet-5 | 77 | 4,501 | 413.9 | 181.4 | 56.2% |
| claude-opus-5 | 8 | 647 | 513.8 | 358.7 | 30.2% |
| claude-fable-5 | 2 | 126 | 648.3 | 377.3 | 41.8% |

Opus-5 workers think proportionally much less and emit roughly **twice** the non-thinking
output per call that Sonnet-5 does. Caveat: non-thinking output is prose *plus* tool
payload, and Source B is all-Sonnet so it cannot split that by model — the 2x may be
larger edits rather than more chatter. But it is the one signal in the data suggesting
brevity rules earn more on Opus lanes than the Sonnet-derived numbers below imply, and it
is the main reason the rules were kept.

## Where output tokens actually go (Source B, 832 API calls, 504,018 output tokens)

| class | tokens | share of output |
|---|---|---|
| thinking | 257,738 | 51.1% |
| tool_use payloads | 145,350 | 28.8% |
| **prose text** | **8,509** | **1.7%** |
| unattributed | 92,421 | 18.3% |

Prose is 30,633 characters across five full build sessions — **37 characters of prose per
API call.** Upper bound if every unattributed token were prose or payload: prose 2.7%,
payload 46%. Either reading gives the same answer.

**The unattributed 18.3% is reported, not absorbed.** Converting characters to tokens at
rates that closed the gap would imply ~1.9 chars/token, which no tokenizer produces for
prose or JSON. Most likely it is the `thinking_tokens` stream frames undercounting (they
are labelled `estimated_`) plus streamed-then-superseded output that never reached the
transcript. `output_discipline.py` carries both readings.

Tool payload breakdown (436,051 chars): `Edit` 49.1% (144 calls, 1,485 ch/call), `Bash`
26.8% (553 calls, 212 ch/call), `Write` 21.1% (23 calls, 4,008 ch/call), `Read` 3.0%.

## What the rules can reach

Measured directly against the behaviours the ticket names:

| rule | measured in the clean set | reachable |
|---|---|---|
| no restated code blocks in prose | **0 chars.** Not one fenced code block in prose in 832 calls | 0 |
| no ceremony / preamble | 1,116 chars of opener lines ("Let me…", "Perfect!") | ~310 tok |
| no narrating a tool call | 1,295 chars | ~360 tok |
| final summary <= ~200 tok | 253 tok average; 426 tok total over cap across 5 sessions | ~426 tok |
| diffs shown once | subsumed by the restatement row — 0 | 0 |
| **total** | | **~1,096 tok** |

That is **0.22% of output** and **0.06% of consumed tokens**. The single largest line item
is the final-summary cap, and even that binds by ~85 tokens per session.

The zero on restated code blocks is the load-bearing result: the behaviour this ticket was
written to stop **does not occur** in observable fleet sessions. The rule is a guard against
regression, not a fix for something happening now.

## Cost of the rules themselves

`harness/prompts/worker.md`: **677 -> 787 tokens (+110, +16.2%)**, `tiktoken cl100k_base`,
the same proxy encoding INSTRUCTION_DIET.md used — valid for a before/after delta on the
same content mix, not for predicting live Claude counts (and per CALL_AUDIT.md's wq#706
note, do not rescale prose counts by the 1.8x code/JSON factor).

Those 110 tokens are prefix: cache-written once per session, cache-read on all ~59
subsequent calls. Per session, at the measured 59.9 calls/session:

| accounting | rules cost | rules save | net |
|---|---|---|---|
| house "consumed" (excl. cache-read) | +110 | −219 | **−109 tok/session, −0.12%** |
| raw, cache-read at full weight | +6,479 | −219 | **+6,260 tok/session** |
| dollars (sonnet-5 rates) | +$0.0026 | −$0.0033 | −$0.0007, break-even |

**So the sign of this change depends on whether the limit counts cache-read tokens.** Under
the wq#629 house definition it is a real but negligible improvement; if cache-read counts at
full weight against the session limit, adding these rules costs more than they save. That
question was not resolved by this ticket and is the thing worth settling before any further
prompt-side token work — it decides whether prefix additions are cheap or expensive in
general, not just here.

## The noise floor swamps the effect — read this before trusting any delta

`output_discipline.py --since <epoch>` splits Source A into pre/post buckets around a
landing timestamp, so the post-change re-measurement does not average new sessions in with
old ones. Running it against the **pre-change population alone**, split at its own median
start time (44 sessions each side), is the control:

| metric | relative change across an arbitrary split of unchanged sessions |
|---|---|
| output tokens / API call | −3.2% |
| non-thinking output / API call | **−15.7%** |
| thinking share of output | +13.9% |
| output share of consumed | +2.1% |

Nothing changed between those two halves. The **−15.7% swing on the exact metric the rules
are supposed to move** is pure between-session variance — ticket mix, archetype, model, how
much editing a task happened to need.

The rules are projected to move that metric by roughly **−1 to −2 tokens per call out of
207.9, under 1%.** That is more than an order of magnitude inside the noise. **A single
post-change re-measurement cannot detect this effect, and any number it produces should not
be believed.** Detecting it needs either a matched-pair design (same tickets, both prompts)
or a session count far beyond what the fleet has produced. This is a limit of the effect
size, not of the instrument.

Related caveat: all 88 Source A sessions start between 2026-08-23T02:25Z and
2026-08-23T09:08Z — usage sidecars are a recent addition, so the baseline is one busy
morning of fleet activity, not a longitudinal sample.

## What landed, and why, given all that

The rules the ticket specifies are in `worker.md` as one five-line block. They were kept
because they are close to free, because they guard against a chattier model regressing (the
Opus 2x above), and because they are what the ticket asked for — not because the measurement
justifies them on savings. The measurement says the opposite, and that is reported here
rather than dressed up.

One rule was drafted and then **dropped**: "prefer `Edit` over re-emitting a whole file with
`Write`." It was not in the ticket, and checking it against the data killed it — only 2 of
23 `Write` calls (4,124 of 85,934 chars, 4.8%) targeted a path the session had already read
or written. The other 21 are legitimate new-file creation. Not worth prefix tokens.

## Quality regression check

The gate asks for no quality regression on probe-verified tickets. No probe was run: there
is no `claude` binary on this worker's `PATH`, so a controlled A/B could not be executed
from here, and fabricating one was not an option.

What can be said from the diff and the data: the block adds no procedural instruction and
removes none. It constrains ceremony, narration, restatement, and summary length. Three of
those four measure at or near zero in real sessions, so there is no established behaviour
for them to disturb. The one that binds — the 200-token summary cap against a measured
253-token average — sits on top of the existing `wq done --notes "<PR url + one-line
summary>"` requirement, which already forces substantive outcome into the ticket record
rather than the final message. The residual risk is that a worker under-reports a partial
failure to stay inside the cap; the "what's left" clause in the rule is there for that, and
it is what a re-measurement should read for.

**This is the gate's weak leg and it should be recorded as such.** A real verdict needs a
matched-pair run, which is the same thing the measured delta needs and is unavailable for
the same reason.

## Follow-ups this ticket did not close

1. **A matched-pair A/B, not a re-measurement.** Per the noise-floor section, re-running
   `--since` after N tickets close will produce a number, and that number will be variance.
   Measuring a sub-1% effect needs the same tickets run under both prompts — which needs a
   `claude` binary on the worker's PATH (there isn't one) or an orchestrator-side probe
   lane. Worth filing only if someone decides a sub-1% output effect is worth the spend;
   the honest read of this report is that it is not.
2. **Does the Max limit count cache-read tokens?** Decides the sign of the table above and
   of every future prompt-side change. Highest-value open question here.
3. **Thinking is 51.9% of output** — the actual lever, ~230x the size of this one. Effort
   tiering already exists (`models.yaml`, CAL-1 / wq#619); nobody has measured what per-
   archetype effort is worth against the fleet's real distribution.
4. **Tool payload is 28.8%**, half of it `Edit` at 1,485 chars/call (old_string + new_string
   means changed text is sent twice by construction). Worth a look before more prose work.
5. **fworker should checkpoint usage on abnormal exit** so `LIMIT`/`CRASH`/`HANG` sessions
   stop being invisible to this baseline — see the coverage caveat above.
