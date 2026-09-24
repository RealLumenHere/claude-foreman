# PILOT 0a0c3351a80e report — self-supervised skill optimization for the DGM cycle

Runner: orch-burn-2-4611f5 (wq#1939), 2026-09-24. Card filled before the run (it was
generated blank); win condition fixed before any number was read. Ran on Lumen, not hathor:
no LLM is involved and `minerva.db` may not leave Lumen (card, Data handling). Nothing wired
into prod.

## Setup

1008 records from `minerva.db` `proposals` (sha256 of the canonical extract
`8e89678797722c589151d44f97a840e0d5414ba64696ea7bdb9959439f589843`): 214 accepted-family
(approved, staged, planned, applied_unverified, deployed, shipped) vs 794 `reject`. Excluded:
`rejected_aged` (aged out, not a judgement), duplicate, defer, piloting, blocked, researching.
Chronological 70/30 split; three rolling-origin folds for variance. Baselines were computed on
the same holdout as the treatment. `python3 run_pilot.py build|run`, output in `results.json`.

## Primary holdout (train 705, test 303, 27 positives, base rate 0.089)

| arm | ROC-AUC |
|---|---|
| baseline: static rubric already in each record (impact up, effort and risk down) | **0.898** |
| baseline: minerva-eval `final_score` | 0.254 (see caveat) |
| treatment: TF-IDF + logistic regression on title and proposed change | 0.874 (label-permutation p = 0.005) |

Rolling-origin folds (rubric / treatment): 0.832 / 0.696, 0.883 / 0.868, 0.933 / 0.901.

## Verdict against the pre-set win condition

Win required treatment >= 0.65, treatment >= best baseline + 0.05, and p < 0.05. The first and
third hold; the second fails in every fold. The text classifier does learn something real
(p = 0.005), but it is 0.024 below a baseline that costs nothing and already exists on the row.

## Caveats, stated because they change how much to trust the number

- `final_score` below 0.5 is an artefact: most accepted-family rows carry no eval result and
  were scored 0, so it ranks accepted rows lower. Read it as "no usable signal", not as an
  inverse relation.
- The acceptance rate in the test windows falls from 0.43 to 0.046 across folds. The pipeline
  changed over time, so patterns learned from older rows are stale by construction.
- The classifier's top positive terms are `timer`, `add`, `script`, `periodically`, `audit`,
  i.e. the phrasing of how proposals are generated, not properties of a good change.
- This measures whether past text predicts acceptance. It does not measure the proposal's
  actual claim, that injecting learned patterns into the DGM prompt yields better proposals.
  That needs two DGM runs (with and without the context) scored on an outcome, which was not run.

RECOMMENDATION: SKIP — treatment AUC 0.874 vs best existing baseline 0.898 on the holdout (needs +0.05); the record's own impact/effort/risk fields already carry the signal.
