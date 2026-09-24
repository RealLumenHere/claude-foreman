# PILOT 98b7d8a0b1f8 REPORT: WikiLoop-style feedback loop

## Result: the signal is too small and too indirect to test the idea
Measured in `minerva.db` (read-only), 2026-09-24:

| | count |
|---|---|
| note_hits rows since 2026-08-15 | 202,773 |
| ...from caller `bench` | 189,220 (93.3%) |
| ...from caller `guard` | 4,780 (2.4%) |
| ...organic (recall-cli 3,744, precheck 2,808, memoryd 1,984, other 237) | 8,773 |
| note_hit_outcomes rows | 990: failure 493, pending 419, success 78 |
| organic hits joined to a success/failure label | 1,364 (968 failure, 396 success) |
| distinct notes those touch | 536 of 2,210 archival notes |

Three problems, each enough on its own:
1. **Volume.** 1,364 labelled organic hits against the 10,000 the card set as the bar.
2. **The label is not about retrieval.** A `failure` is set when the episode's `--failed` field is non-empty
   (example row: "episode --failed non-empty"). That says the work hit trouble, not that the retrieved note was wrong.
3. **The bulk is circular.** 93% of hits are the recall bench querying the golden set. Learning a prior from them just
   re-encodes the eval set, so any before/after gain on golden v4 would be leakage.

## The proposal's premises
"No eval harness" is stale: golden v4 exists (see the aac675199d1e report). "No downstream feedback" is also stale in the narrow sense:
note_hits and note_hit_outcomes shipped with wq#576. They are just not yet the right shape or size.
WikiLoop itself (arXiv 2607.26604) learns structural edits of an LLM-Wiki from downstream utility with a trained Navigator, which is a
research build, not a drop-in for pgvector.

## What would change the answer
Retrieval-specific labels (for example a corrector or ack event tied to the hit's episode), at least 10,000 organic labelled hits, and a
held-out split that excludes bench traffic. Both are cheap to check again from the same tables.

RECOMMENDATION: WATCH
