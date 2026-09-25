# PILOT 76c39dd5afde report: adaptive cross-task memory transfer for research pipelines

Runner: wq#2436 BUILD worker, 2026-09-25. I measured the baseline. I did not run the
treatment arm. Nothing was built or wired into prod.

Node deviation: the card said hathor, but `fleet-exec run hathor -- hostname` was denied
with `grant:grant_missing_env`. That is the same grant gap that pilot 0eed2f5bdb92 hit.
The baseline only reads a journal, so I ran it on Lumen.

## Precondition probe

| check | result |
|---|---|
| `lumen-orchestrator` unit files | 0 installed (it was parked with the research mill, see pilot 0d51b20dba5f) |
| `lumen-fleet-{openrouter,cerebras,mistral}` | all 3 are active and running `/home/lumen/agent/lumen_fleet_researcher.py` |
| cross-task context already in the prompt | yes: `local_retrieval: injected N chars of corpus context` on 251/251 tasks, median 3865 chars |
| `lumen-dream.timer` | active, next run 2026-09-27 |

The proposal says the research pipelines run "without sharing" anything across tasks.
The journal shows otherwise for the one live pipeline: every task already gets about
3.9K chars of prior-corpus context. Prior research content is already transferred. What
isn't transferred is extracted "strategies/snippets", and that is the only new part of
the proposal.

## Baseline (fixed sample: 364 scored attempts, 2026-09-23 00:20 to 09-24 20:21, sha256 8f9604bb...)

From `measure_baseline.py` → `baseline.json`:

| metric | value |
|---|---|
| grounding pass rate per attempt (primary) | **69.3%** (178 pass / 79 fail) |
| grounding fails with unresolved citations | 26 |
| grounding fails with 3 or more URLs never seen in context | 53 |
| quality-gate fails | 107, **all degenerate** (response under 200 chars) |
| unique tasks done / requeued / rejected | 198 / 44 / 22 (task success 75.0%) |
| web_search augmentation | **0 of 251** (`no augmentation (budget/key/results)`) |
| failed attempts that prompt context could address | 79 of 364 (**21.7%**) |

## What the numbers say

1. 107 of the 186 failed attempts (58%) are empty or near-empty completions from the
   provider. No prompt snippet can fix those.
2. The other 79 failures are all citation grounding failures: unresolved references,
   or 3 to 7 URLs the model was never shown. Web search returned nothing on all 251
   tasks, so the only real URLs the model sees come from the corpus block. The likely
   cause is the dead web-search augmentation, not missing cross-task strategy. I did
   not test that. It is a reading of the logs, not a measured result.
3. So a snippet-injection arm is capped at +21.7 pts, and that cap assumes it removes
   every citation failure. A new service with a new table and a schema migration is a
   heavy way to go after a failure mode with a cheaper candidate cause upstream.

## Why the treatment was not run

- The researcher source (`/home/lumen/agent/fleet_graph.py`, including its grounding
  check) is outside the BUILD-worker sandbox. Read, find and grep were all refused. A
  faithful arm has to reuse the real prompt and the real grounding scorer. Building a
  look-alike scorer would mean guessing its thresholds.
- hathor was unreachable, as noted above.
- The prior-art search (`fleet-search web`, two queries) returned 0 results, so I could
  not identify the paper's released method or code.

## If someone still wants it

1. Restore web_search augmentation, then re-run `measure_baseline.py` on a fresh journal.
   If citation failures drop, most of the addressable 21.7% is gone.
2. If grounding failures stay above about 20% after that, run the real arm from a lane
   that has `/home/lumen/agent` read access. Take 30 pinned topics from `Done (graph)`
   lines, prepend a block of snippets mined from notes that passed grounding, and score
   with the unmodified grounding check. Win bar: +10 pts.

RECOMMENDATION: SKIP — baseline grounding pass 69.3% (178/257); 58% of failed attempts (107/186) are degenerate provider responses; addressable ceiling 21.7% of attempts (79/364), all citation failures, while web_search augmentation was 0/251; cross-task corpus context already injected on 251/251 tasks (median 3865 chars); treatment not run (agent source outside sandbox, hathor grant_missing_env). Re-open if grounding fails stay >20% after web search is restored.
