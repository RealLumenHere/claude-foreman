# PILOT 54a55b1f5ec7 report — LLM-calibrated grey-box simulation of the research pipeline

Runner: orch-burn-A (wq#2420), 2026-09-24. No arm was scored and hathor was not used: the outcome data
a calibration needs does not exist yet, so nothing was built.

## What was measured

Read-only queries against `minerva.db` (proposals, changelog):

| check | result |
|---|---|
| proposals | 1687 rows, created 2026-04-22 to 2026-09-24 |
| terminal outcome on file | 12 deployed (9) or shipped (3); `deployed_at` is set on 12 rows |
| applied but never verified | 50 (`applied_unverified`) |
| changelog | 39 rows over 38 proposals, 0 reverted |
| `eval_passed` | 292 ones, 1395 zeros across all 1687 rows, so zero also covers rows that were never evaluated |
| proposals with a wq ticket | 46 |
| proposal path in the card | `/data/lumen/minerva/calibrate_sim.py`; `/data/lumen` does not exist, Minerva is `/home/lumen/minerva` |

## Findings

1. A calibration needs labelled outcomes with both classes present. There are 12 outcome rows and no
   reverted change among 39, so there is no failure class to fit and nothing to hold out for a test split.
2. The deferral reason holds: the gaps the fleet has are shallow pre-scoring and noisy research-to-action
   conversion, and both sit upstream of any simulation. Of 1687 proposals, 12 reach a measured outcome.
3. `eval_passed` cannot serve as a label as it stands, because the default and a real failure are both 0.
4. A simulator that an LLM tunes against 12 points would produce numbers with no way to tell fit from
   noise. Adding a timer unit for it would add a recurring LLM cost against that.

## If someone still wants it

First make outcomes measurable: verify the 50 `applied_unverified` rows and record a real
pass/fail/reverted result on each. Revisit when there are a few hundred outcome rows with both classes.

RECOMMENDATION: SKIP
