# PILOT REPORT — 0eed2f5bdb92

**Add Writable Knowledge-Graph Memory to Complement pgvector**

wq#1559 / wq#1560 · run 2026-09-08 18:42 PDT on hathor · agent work-1559-ae5413

## Question

The proposal was deferred on an empirical question, quoted from its own defer reason:
whether a writable knowledge-graph memory "will meaningfully improve retrieval/
adaptation over the existing pgvector+rerank pipeline", with proposal dedup named as
the concrete task. This pilot answers that and nothing wider.

## Setup

| | |
|---|---|
| Node | hathor (24 threads, 60G, load 0.00) |
| Fixed sample | 50 proposals the live dedup gate suppressed as duplicates, each labeled with the `parent_id` it was suppressed against |
| Candidate pool | 605 rows of `proposal_dedup` (nomic-embed-text, 768d) |
| Distinct parents | 21 |
| Primary metric | R@1 — is the true parent ranked first |
| Wall clock | ~4 min of the 90 min budget |
| Cost | $0. No LLM calls. 50 local embeddings on Lumen's ollama; both arms ran model-free on hathor |

Only the query embeddings were computed on Lumen, because that is where
nomic-embed-text lives. Both arms then ran on hathor over the same vectors and text,
so neither arm gained anything from where it executed. `minerva.db` was never copied;
hathor received a purpose-built extract of ids, titles, `proposed_change`, and vectors.

## Arms

- **A — baseline.** Cosine over the stored vectors, then the entity-share gate. This
  is the pgvector+rerank path as `minerva_dedup.check` actually implements it.
- **B — treatment.** A writable knowledge graph in SQLite: an adjacency table of
  proposal↔entity edges, IDF-weighted, scored by weighted shared-entity overlap.
  3,076 edges over 1,257 entities, 252 KB.
- **C — fused.** Equal-weight fusion of A and B, because the proposal says
  *complement* pgvector, not replace it. Reporting only A vs B would answer a
  question the proposal did not ask.

## Results

| Arm | R@1 | R@5 | MRR | ms/query |
|---|---|---|---|---|
| A baseline dense+rerank | **0.940** | 0.980 | 0.957 | 141.1 |
| B graph only | 0.820 | 0.960 | 0.872 | **0.3** |
| C fused (A+B) | 0.920 | 0.980 | 0.947 | 173.7 |

The graph does not beat the baseline on the metric the proposal named. Fusion — the
form the proposal actually asks for — is *worse* than the baseline it was meant to
complement: R@1 0.940 → 0.920, MRR 0.957 → 0.947, at 23% more latency.

## The result is weaker than it looks, in the baseline's favour

**The ground truth was produced by the baseline.** These 50 labels are the parent each
proposal was suppressed against by the live cosine+entity-share gate. Arm A is being
scored against its own past output, so its 0.940 is inflated by construction and is
NOT a clean measure of dedup quality. Any arm that is not A starts at a structural
disadvantage on this sample.

Two things keep the run informative anyway:

1. A does not score 1.000. The index has grown by 205 rows since the earliest
   suppression, so later near-duplicates now outrank some original parents. The
   circularity is strong, not total.
2. The bias runs *against* B, and B still recovers 82% of the baseline's own
   rank-1 decisions with no embedding model at all. So the finding "B does not beat A"
   is the one direction this sample can support honestly. The reverse claim — that A
   is genuinely 12 points better — is not supported and should not be quoted.

What this pilot cannot do is show the graph *winning*, because a sample labeled by A
has no way to reward a correct retrieval that A missed. Answering that needs labels
from a source that is not the dedup gate — hand-labeled duplicate pairs, or pairs
drawn from proposals a human later merged. That is a different, larger pilot.

## Verdict

For the task the proposal named — proposal dedup — the graph adds nothing the
existing path does not already do, and the complement form measurably degrades it.
The one real advantage is speed (0.3 ms vs 141 ms per query, ~470x), but dedup
latency has never been a complaint: the gate runs once per generated proposal, and
141 ms of that is a local cosine scan, not a bottleneck anyone reported.

The proposal's *other* claimed use — episodic lesson memory and adaptive prompting —
was not tested here and is not judged by this report. It is also not what the defer
reason asked about.

RECOMMENDATION: SKIP

Skipping the knowledge-graph memory for proposal dedup. It loses to the existing
pgvector+rerank path on R@1 (0.820 vs 0.940 standalone; 0.920 vs 0.940 fused), on a
sample whose labels already favour the baseline — so the loss is real even though the
margin is not trustworthy. The speed advantage is large but buys nothing at a call
site that runs once per proposal. If the graph is revisited, it should be for the
episodic-lesson use case against hand-labeled ground truth, not re-run against labels
the dedup gate itself produced.

## Artifacts

- `CARD.md` — the experiment card this ran from
- `build_sample.py` — extract builder (Lumen)
- `run_pilot.py` — both arms (hathor)
- `sample.json` — the fixed sample, 50 queries / 605 candidates
- `results.json` — raw numbers
- Remote working copy: `hathor:/home/hathor/pilots/0eed2f5bdb92/`

Nothing from this pilot was wired into prod. Per the lane's standing rule, an ADOPT
would have fed minerva-factory PLAN and been built through the normal path; a SKIP
changes nothing.
