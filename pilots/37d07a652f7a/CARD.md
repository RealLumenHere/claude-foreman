# PILOT 37d07a652f7a: Evaluate CubicQuant for LiteLLM quantization

Written at run time. `minerva_defer_review` never wrote `pilots/37d07a652f7a/CARD.md` (same defect as ISSUE-LOG #257), so this card is
filled in from the proposal in `minerva/proposals/pending/` before any measurement, per that precedent.

| | |
|---|---|
| Proposal | `37d07a652f7a` |
| Node | Lumen (nothing here needed hathor: the questions were answered from the queue's own data and the paper) |
| Wall-clock budget | 60 min |
| Report | `pilots/37d07a652f7a/REPORT.md` |

## Why this is a pilot
Promising but XL effort and L risk, with no implementation plan or benchmarks.

## Hypothesis
CubicQuant can be applied as a quantization step in the LiteLLM gateway to a local model and improve throughput on hardware Lumen has.

## Primary metric
Whether the three preconditions hold: (1) the gateway holds weights, (2) released code exists, (3) target hardware runs the format. A win needs all three.

## Fixed sample
The paper (arXiv 2608.06763), the LiteLLM config and the node inventory.

## Baseline (measured first)
How local inference runs today (llama.cpp on CPU).

## Treatment
Would be a quantization prototype on hathor. Not built, see kill criteria.

## Kill criteria
Any precondition false: stop, SKIP.
