# PILOT 18d66c25806e report — shared-directory test-time communication for scout/intel agents

Runner: wq#3139, 2026-10-01. The card's "Baseline does not reproduce" kill criterion fired before the first
command. Neither arm was scored and hathor was not used. Every artifact the baseline commands name is missing,
so the baseline cannot run. Writing those artifacts here would mean the runner invents the measurement plan,
which `research/DEFER_PILOT_LANE.md` §3.1a item 2 forbids.

## What was checked (Lumen, read-only, 2026-10-01)

| card reference | result |
|---|---|
| `/home/lumen/agent/scout_tasks.jsonl` (the pinned 60-task sample) | does not exist, so there is no sha256 to pin and no task_id set |
| `/home/lumen/agent/run_scout_sample.py` (both arms) | does not exist |
| `/home/lumen/agent/score_conversion.py` (both arms) | does not exist |
| `/home/lumen/agent/outbox/<task_id>.json` (metric source) | the `outbox/` directory does not exist |
| `action_item` field (metric numerator) | 0 hits in `github_scout.py`, `lumen_intel.py`, `llm_project_scout.py`, `scout_to_prompts.py` |
| `TEAM_COMM_ENABLED` / `team_comm/` | absent; expected, since this is the treatment the pilot was meant to add |
| `lumen-scout.service` | runs `/home/lumen/autonomy/lumen_scout_graph.py`, which is not under `/home/lumen/agent/` as the card assumes |
| `paired_eval` (DGM v2, named in the proposal) | appears only as prose in `minerva/minerva_proposal.py:67,125` (a prompt context list); no callable harness found under `foreman/harness` or `bin` |

## Findings

1. The spec generator wrote the card against a harness that does not exist. The sample file, runner, scorer,
   outbox and `action_item` field are all invented. The card passed `validate()` because validation checks
   form (a hypothesis with a number, a pinned sample, a command in each arm) and never checks that a named
   path exists.
2. The deferral reason still stands. Scout/intel output has no per-task `action_item` signal today, so
   there is no research-to-action conversion number to move. Running this A/B first needs a task set with a
   defined success criterion and a scorer, and that is the gap the deferral named.
3. To make the pilot runnable, someone has to build a fixed scout task set, a sample runner that calls
   `lumen_scout_graph.py` once per task, and a scorer over a real output field. That is a build ticket in
   its own right, separate from the shared-directory layer, and it would need its own review.

## Follow-up

- Dispatch defect: the wq#3118 gate `harness/pilot_spec_author.py:442` `lint_card()` already catches this
  card. Run against it, it returns 15 problems, including all three missing inputs. But nothing outside
  its tests calls it (`grep -rln lint_card` finds only `pilot_spec_author.py` and
  `tests/test_pilot_spec_author.py`), so the card was dispatched anyway. Wiring it in before a pilot ticket
  is opened would have stopped wq#3139 before dispatch. The gate also flags arm outputs such as
  `results/baseline.jsonl` that only exist after a run, so the wiring needs to tell input paths from
  output paths. Logged in `kb/ISSUE-LOG.md`.
- Revisit this proposal after scout/intel have a per-task outcome field and a pinned task set.

Measured numbers: baseline rate = not measurable (0 of 60 tasks runnable, sample file absent);
treatment rate = not run; delta = n/a; wall-clock used ≈ 10 min of 90; tokens used well under the
150000 budget.

RECOMMENDATION: WATCH
baseline_conversion_rate=n/a treatment_conversion_rate=n/a delta=n/a tasks_run=0/60 kill_criterion=baseline_does_not_reproduce
