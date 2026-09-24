# PILOT 2fcee35b2861 report — state-matched routing and contextual self-distillation for the prompt engine

Runner: orch-burn-2-4611f5 (wq#1790), 2026-09-24. No experiment could be run: the card's
kill criterion "Baseline does not reproduce -> stop, report the baseline defect" applies.
Nothing was built and nothing was wired into prod.

## What was measured

| check | result |
|---|---|
| a running prompt-engine unit | none. `systemctl list-units --all` has only `lumen-prompt-forge` (wq#957, a different tool: an HTTP prompt generator for people) |
| last refill that added prompts | 2026-07-19 05:43 (`Added 2 prompts. Queue now: 2`) |
| last log line | 2026-08-28 06:42, `Added 0 prompts. Queue now: 0` |
| `agent/research_index.db` (the engine's `RESEARCH_INDEX`) | does not exist |
| conversation state, stage/intent, per-prompt outcome, multi-turn logs in the engine | none. `agent/prompt_engine.py` (418 lines) picks a topic from static banks and writes a single-shot prompt file (`make_research_prompt`, `write_prompt`, `refill_queue`) |

## Why the hypothesis cannot be tested

1. The proposal's premise ("keeps the research queue full 24/7") is stale. The engine has not
   run since August 28 and has not added a prompt since July 19.
2. It describes a system with "conversation state" and "successful multi-turn logs in
   minerva.db". The engine has neither. Prompts are single-shot, and it never reads or writes
   minerva.db, so there is nothing to route on and nothing to distil from.
3. There is no outcome metric per prompt. The reason it was deferred (no eval harness) is
   still true, and building the harness is the real work, not the router.

A/B numbers would have had to be invented. None were.

## If someone still wants this

Re-file it against the live path (whatever now produces research and fleet prompts, e.g. the
prompt-forge history plus ticket outcomes), and start with the measurement loop: an accepted /
rejected label per generated prompt. Only then is a router or self-distillation testable.

RECOMMENDATION: SKIP — target component is dormant and has no state or outcome data; 0 of 0 measurable arms.
