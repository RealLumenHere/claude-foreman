# PILOT 751b934599d5 report — Gromov-Wasserstein quantization for research-embedding clustering

Runner: orch-burn-B-3f4634 (wq#2437), 2026-09-24. Card filled before the first scored call.
Ran on Lumen scratch in a venv (POT 0.9.7, scikit-learn 1.9.1), not hathor: hathor has no numpy or pip3, and the
extract is fleet data that stays on Lumen. The extract (embeddings only, hashed ids) is not committed.

## What was measured

Extract: `research_docs.embedding` for all 11,800 rows, 768-dim nomic-embed-text, sha256
`29e1f2f83fe1c42a86ddcccd9a1d6c96fdd7e22da7813479175fb074e70a043c`. Seven rows are all-zero vectors (failed embeds)
and were dropped, leaving 11,793. Per seed: 4,000 index vectors, 500 disjoint query vectors, k = 64, ground truth is the
exact cosine top-10 inside the index. Search routes a query to the nearest cluster centroids and scans only those clusters.

Recall@10 at 2 probed clusters, 5 seeds:

| seed | k-means | GW quantization | GW minus k-means |
|---|---|---|---|
| 0 | 0.626 | 0.368 | -0.258 |
| 1 | 0.659 | 0.378 | -0.282 |
| 2 | 0.641 | 0.355 | -0.286 |
| 3 | 0.637 | 0.361 | -0.276 |
| 4 | 0.638 | 0.375 | -0.262 |

- Mean difference **-0.273**; GW ahead in **0 of 5** seeds. The card's win was +0.03 and at least 4 of 5.
- Index fraction scanned at 2 probes: GW 0.954 of k-means, so GW did not win by scanning less.
- Same picture at 1 probe (0.21-0.23 vs 0.45-0.49) and 4 probes (0.56-0.59 vs 0.77-0.80).
- Cost: the GW arm starts from a k-means fit (38-40 s, n_init = 10) and added 48-55 s on top of it at n = 4,000, peak memory 715 MB.
  The n x n distance matrix at all 11,793 rows would be about 1.1 GB in float64.
- GW reassigned 80% of the points that k-means had placed (0.797 to 0.812 per seed).

## Verdict under the rule fixed in the card

SKIP: difference far below +0.01, and the GW step adds fit time on top of k-means instead of replacing it.

## Why it likely loses (interpretation, not tested)

Gromov-Wasserstein matches distance structure and is blind to where points sit in the space. Assigning a point by its coupling
row groups points with similar distance profiles, not nearby points, and routing by cluster mean then misses. That fits the
80% reassignment and the recall drop, but I did not run an ablation.

## Limits

- The proposal names no algorithm details. This is my reading of GW quantization: coupling to a 64-point space initialised from k-means,
  5 rounds of POT `gromov_wasserstein` plus a barycentre update of the quantized distances, argmax assignment. A different variant could behave differently.
- 4,000-vector subsample, one k, nomic-embed-text vectors. The proposal says Lumen retrieves with BGE-M3; `index_research.py` embeds the research docs with nomic-embed-text.
- Retrieval utility only. Cluster quality by other measures was not scored.

## Side finding

`research_docs` holds 7 all-zero embeddings (11,800 rows), presumably failed embed calls stored as zero vectors. They rank arbitrarily in cosine search.

RECOMMENDATION: SKIP — recall@10 at 2 probes is 0.273 lower than k-means (GW ahead in 0 of 5 seeds; needed +0.03 in >= 4), and the GW step adds 48-55 s to the k-means fit at n = 4,000; one GW variant, one k.
