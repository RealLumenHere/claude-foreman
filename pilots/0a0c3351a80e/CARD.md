# PILOT 0a0c3351a80e — Add self-supervised skill optimization to Lumen DGM cycle

Experiment card. Generated 2026-09-24T12:47:55+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `0a0c3351a80e` |
| Deferred | 2026-08-09 |
| Impact / effort / risk | M / M / S |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/0a0c3351a80e/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#1939, card /home/lumen/foreman/pilots/0a0c3351a80e/CARD.md. Original: Technique is academic and we lack clear evidence of an existing 'skill table' with evaluation history; defer until we can pilot a simple pattern-matching baseline.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner before any scored call, per this card's "edit it before the run" clause: the card was generated blank.)

A lightweight text-pattern classifier (TF-IDF + logistic regression) trained on older proposal records ranks later proposals by eventual acceptance with ROC-AUC >= 0.65 and at least 0.05 above the best static baseline already present in each record, on a time-ordered holdout. If not, there is no learnable 'successful pattern' to inject into the DGM prompt.

## Primary metric

ROC-AUC on the chronological holdout (last 30 percent of the sample; unitless, 0.5 = chance). Win, fixed before the run: treatment AUC >= 0.65 AND treatment AUC >= best baseline AUC + 0.05 AND label-permutation p < 0.05 (200 permutations). Three rolling-origin folds are reported for variance, not for the verdict.

## Fixed sample

1008 proposals from `minerva.db` `proposals`, ordered by created_at then id. Positive (214): status in approved, staged, planned, applied_unverified, deployed, shipped. Negative (794): status `reject`. Excluded on purpose: `rejected_aged` (aged out, not a judgement), `duplicate`, `defer`, `piloting`, `blocked`, `researching`. Extract (id, created_at, status, title, proposed_change, impact, effort, risk, eval_result) written on Lumen to `/tmp/orch-burn-2-4611f5/pilot_0a0c_sample.json`, never copied to another node; sha256 of the canonical JSON `8e89678797722c589151d44f97a840e0d5414ba64696ea7bdb9959439f589843`.

## Baseline (measure FIRST)

Today Lumen has no learning loop, so the baselines are what each record already carries. Both run on the same holdout before the treatment number is read.

```
python3 /home/lumen/foreman/pilots/0a0c3351a80e/run_pilot.py build
python3 /home/lumen/foreman/pilots/0a0c3351a80e/run_pilot.py run   # baseline_static_rubric_auc (impact up, effort and risk down), baseline_eval_final_score_auc (minerva-eval)
```

## Treatment

TF-IDF (unigrams, 3000 terms, min df 3) + class-weighted L2 logistic regression in numpy, trained only on records before the holdout. No LLM, no prod wiring.

```
python3 /home/lumen/foreman/pilots/0a0c3351a80e/run_pilot.py run   # treatment_text_pattern_auc, perm_p_value
```

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

Lumen currently generates self-improvement proposals via lumen-dgm and evaluates them via minerva-eval, but there is no explicit historical skill evaluation store or learning loop from past proposals.

## Proposal — proposed change

Create a new timer-driven service (lumen-skill-optimizer) that reads historical proposal records from minerva.db, applies a lightweight self-supervised technique (e.g., topic clustering or classification) to identify successful patterns, and periodically injects these patterns as additional context into the DGM proposal prompt (via lumen-prompt-engine or direct DGM script edit).

---

The report at `/home/lumen/foreman/pilots/0a0c3351a80e/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
