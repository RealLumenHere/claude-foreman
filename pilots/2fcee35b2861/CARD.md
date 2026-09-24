# PILOT 2fcee35b2861 — Add state-matched routing and contextual self-distillation to prompt engine

Experiment card. Generated 2026-09-24T12:47:55+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `2fcee35b2861` |
| Deferred | 2026-08-08 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/2fcee35b2861/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#1790, card /home/lumen/foreman/pilots/2fcee35b2861/CARD.md. Original: Lumen lacks an eval harness to verify self-distillation gains, and the paper's methods target model training rather than prompt engineering; revisit once a measurement loop exists and feed health monitoring is in place.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner before any scored call, per this card's "edit it before the run" clause: the card was generated blank.)

A state-router in front of lumen-prompt-engine's prompt selection raises the share of generated research prompts whose report a reviewer accepts by at least 10 points over today's static topic-bank pick, measured on the same fixed set of prompts.

## Primary metric

Reviewer-accepted share of prompts (accepted / issued, unit: percent, per arm). Win: treatment >= baseline + 10 points on the same sample. Not measurable today, see Baseline.

## Fixed sample

None can be pinned. The engine keeps no per-prompt outcome, conversation state or multi-turn log (it builds single-shot prompt files from topic banks: make_research_prompt, write_prompt), so there is no set of 'successful multi-turn logs' to replay or to score against.

## Baseline (measure FIRST)

Baseline reproduction check, run first. It must show the engine emitting prompts today; it does not.

```
systemctl list-units --all | grep -i prompt          # no prompt_engine unit; only lumen-prompt-forge (a different tool, wq#957)
grep 'Added [1-9]' /home/lumen/agent/logs/prompt_engine.log | tail -1   # last real refill 2026-07-19
tail -1 /home/lumen/agent/logs/prompt_engine.log                        # last line 2026-08-28: 'Added 0 prompts. Queue now: 0'
```

## Treatment

Not built. The kill criterion for a baseline that does not reproduce applies, so nothing was built.

## Kill criteria

<!-- When to stop early WITHOUT asking permission. Stopping on a kill criterion is a
successful pilot whose answer is SKIP or WATCH, not a failed one. -->

- Wall-clock exceeds 90 min -> stop, report what was measured.
- Baseline does not reproduce -> stop, report the baseline defect. Do not proceed.
- Treatment incomplete at budget -> report partial numbers, RECOMMENDATION: WATCH.

## Data handling

`/home/lumen/minerva` is not a broker read root (credentials are mirrored into its
context sections) and `minerva.db` is never copied to a pilot node. A pilot that
needs fleet data takes a purpose-built extract of only the columns it needs, written
on Lumen, and the extract is listed here.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod. An ADOPT verdict feeds minerva-factory PLAN
like any other accept and is built through the normal path, with its own review.

## Proposal — current state

Lumen's prompt engine (lumen-prompt-engine) keeps the research queue full 24/7 with static topic banks; there is no state-aware prompt selection or self-improvement loop for multi-turn interactions.

## Proposal — proposed change

Augment lumen-prompt-engine with a state-router that selects prompt variants based on conversation state (stage, intent, recent outcomes), and add a timer-driven self-distillation job that samples successful multi-turn logs from minerva.db, uses an LLM to generate improved prompt variants, and evaluates them via A/B metrics before activation.

---

The report at `/home/lumen/foreman/pilots/2fcee35b2861/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
