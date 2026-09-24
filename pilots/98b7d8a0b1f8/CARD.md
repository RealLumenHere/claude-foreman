# PILOT 98b7d8a0b1f8: Adapt WikiLoop feedback loop to auto-update memory and pgvector index

Written at run time. `minerva_defer_review` never wrote `pilots/98b7d8a0b1f8/CARD.md` (same defect as ISSUE-LOG #257), so this card is
filled in from the proposal in `minerva/proposals/pending/` before any measurement, per that precedent.

| | |
|---|---|
| Proposal | `98b7d8a0b1f8` |
| Node | Lumen (nothing here needed hathor: the questions were answered from the queue's own data and the paper) |
| Wall-clock budget | 60 min |
| Report | `pilots/98b7d8a0b1f8/REPORT.md` |

## Why this is a pilot
The paper is not directly installable, and Lumen is said to lack an eval harness and downstream feedback signals.

## Hypothesis
Lumen already logs enough downstream feedback, tied to retrieved notes, to learn from and to evaluate a feedback loop honestly.

## Primary metric
Count of non-bench, non-guard note hits that join to a success/failure outcome label (a win is >= 10,000, with a retrieval-specific label).

## Fixed sample
`minerva.db` tables note_hits, note_hit_outcomes, archival_notes as of 2026-09-24 (read-only).

## Baseline (measured first)
SQL counts by caller and outcome.

## Treatment
Would be: re-rank with the feedback prior on golden v4. Not built, see kill criteria.

## Kill criteria
If organic labelled hits are far below 10,000, or the label is not retrieval-specific, or the signal is circular with the bench: stop, WATCH.
