# PILOT 751b934599d5 — Implement Gromov-Wasserstein Quantization for pgvector Research Embedding Clustering

Experiment card. Generated 2026-09-23T09:11:12+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `751b934599d5` |
| Deferred | 2026-08-16 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/751b934599d5/REPORT.md` |

## Why this is a pilot and not a research prompt

The algorithm is promising but requires validation on Lumen's specific embedding distribution and hardware constraints (no AVX-512). Also, the benefit over existing retrieval is uncertain; a pilot study is needed before integration.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner (wq#2437) BEFORE any scored call; the card was generated blank.)

Clusters from Gromov-Wasserstein quantization of Lumen's research embeddings give better cluster-pruned retrieval than k-means at the same k: recall@10 at 2 probed clusters is at least 0.03 higher, without scanning materially more of the index.

## Primary metric

Mean paired difference (GW minus k-means) in recall@10 at 2 probed clusters, over 5 seeds, against exact cosine top-10. Unit: recall points (0-1). Also recorded: fraction of the index scanned, fit time, peak memory.
Verdict rule, fixed now: ADOPT if difference >= +0.03, GW ahead in at least 4 of 5 seeds, and GW scans <= 1.25x the k-means fraction. WATCH if difference is >= +0.01 but ADOPT fails. SKIP otherwise, or if one fit at n=4,000 takes more than 10 min.

## Fixed sample

Purpose-built extract, written on Lumen, never copied elsewhere: `research_docs.embedding` (nomic-embed-text, 768-dim) for all 11,800 rows, with a hashed filepath id and category. 7 rows are all-zero vectors (failed embeds); they are dropped before anything runs, leaving 11,793. No text. sha256 of the float32 matrix `29e1f2f83fe1c42a86ddcccd9a1d6c96fdd7e22da7813479175fb074e70a043c`. Vectors L2-normalised.
Per seed s in 0..4: 4,000 random index vectors and 500 disjoint random query vectors; k = 64. Ground truth is the exact cosine top-10 of each query inside the index set.
Note: the proposal says Lumen retrieves with BGE-M3; `index_research.py` embeds research docs with nomic-embed-text, which is what is measured.

## Baseline (measure FIRST)

k-means (scikit-learn, k=64, n_init=10, seed s), IVF-style search: route a query to the nearest centroid(s) by cosine, scan only those clusters' members, return the top-10. Computed for each seed before the GW arm.

## Treatment

GW quantization of the index set as a k-point metric-measure space with POT 0.9.7: distances C1 = Euclidean distance between the 4,000 unit vectors, uniform weights; quantized space starts from the k-means centroid distances and the k-means cluster proportions. The coupling is initialised from the k-means assignment, then 5 rounds of (POT `gromov_wasserstein`, square loss) and the barycentre update of the quantized distance matrix from the coupling. Hard assignment = argmax of the coupling row. Routing then uses the mean vector of each GW cluster, same as the baseline. Runs in a venv under `/tmp` on Lumen; no service is created.

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

Lumen currently uses BGE-M3 hybrid retrieval with RRF fusion for memory and research indexing (lumen-research-index embeds research corpus into pgvector). Clustering of embeddings is not explicitly performed; retrieval is based on similarity search.

## Proposal — proposed change

Add a Python script that implements Gromov-Wasserstein quantization (using POT library) to cluster pgvector research embeddings. Integrate as a new timer-driven service (e.g., lumen-gw-cluster) that periodically clusters embeddings and stores cluster assignments in a new table or metadata column.

---

The report at `/home/lumen/foreman/pilots/751b934599d5/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
