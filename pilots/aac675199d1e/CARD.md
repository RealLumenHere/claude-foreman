# PILOT aac675199d1e: Pilot AgentMemBench as a memory eval harness for Lumen

Written at run time. `minerva_defer_review` never wrote `pilots/aac675199d1e/CARD.md` (same defect as ISSUE-LOG #257), so this card is
filled in from the proposal in `minerva/proposals/pending/` before any measurement, per that precedent.

| | |
|---|---|
| Proposal | `aac675199d1e` |
| Node | Lumen (nothing here needed hathor: the questions were answered from the queue's own data and the paper) |
| Wall-clock budget | 60 min |
| Report | `pilots/aac675199d1e/REPORT.md` |

## Why this is a pilot
Runnable code and a task format compatible with LiteLLM-backed models are not verified, and Lumen is said to have no memory eval harness.

## Hypothesis
AgentMemBench adds a memory eval harness Lumen does not already have, and is runnable against Lumen's pipeline through the LiteLLM gateway.

## Primary metric
Two yes/no facts: (1) a runnable repo is identifiable, (2) Lumen has no comparable harness. A win needs (1) yes and (2) yes.

## Fixed sample
The paper (arXiv 2608.00009, full text) and the live `minerva/eval/` files.

## Baseline (measured first)
`eval/sched_baseline.json` headline, `eval/golden_v4_frozen_20260814.jsonl`, `lumen-recall-bench.timer`.

## Treatment
Read the paper for a repo link and the task format.

## Kill criteria
No repo URL in the paper text, or the harness gap already closed: stop, SKIP.
