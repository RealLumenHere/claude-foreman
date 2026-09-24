# PILOT 4af69b85ad58: Implement causal audit of latent agent communication

Written at run time. `minerva_defer_review` never wrote `pilots/4af69b85ad58/CARD.md` (same defect as ISSUE-LOG #257), so this card is
filled in from the proposal in `minerva/proposals/pending/` before any measurement, per that precedent.

| | |
|---|---|
| Proposal | `4af69b85ad58` |
| Node | Lumen (nothing here needed hathor: the questions were answered from the queue's own data and the paper) |
| Wall-clock budget | 60 min |
| Report | `pilots/4af69b85ad58/REPORT.md` |

## Why this is a pilot
Research method not validated for Lumen's architecture, and current evals already measure outcomes.

## Hypothesis
Lumen's agents exchange latent (hidden-state or KV) messages that a causal audit can intervene on.

## Primary metric
Count of latent channels in Lumen's agent code (a win is >= 1).

## Fixed sample
Code under /home/lumen/agent* and the audit paper (arXiv 2607.26773).

## Baseline (measured first)
grep for past_key_values, kv_cache, hidden_states, output_hidden_states, inputs_embeds.

## Treatment
Would be the audit script on agent5 messages. Not built, see kill criteria.

## Kill criteria
Zero latent channels: stop, SKIP.
