#!/usr/bin/env python3
"""wq#1684 pilot a7d04e5ebe7d — Complementary Memory Traces (CMT) for multi-hop
retrieval vs single-hop dense retrieval.

Runs on Lumen (see REPORT.md "Node substitution" note — same precedent as
ISSUE-LOG #257 / pilot 2c11123fab8b: fleet-exec grant to hathor is unavailable
to this ticket's `needs:` tags). Pure stdlib: no numpy, no model, no network.

Reuses the SAME fixed sample as pilot 0eed2f5bdb92 (proposal dedup: 50 labeled
queries, 605-row index, live nomic-embed-text vectors, threshold/entity_share
copied from minerva_dedup) so this pilot's numbers sit on an already-vetted,
real-data retrieval-quality harness instead of a synthetic one built from
scratch under a 90-minute budget. Both arms read the same sample.json.

TASK: for each of the 50 suppressed-duplicate proposals, retrieve its true
parent_id out of the 605-row index.

ARM A (baseline, what Lumen does today): single-hop cosine similarity over the
stored embeddings, exactly as minerva_dedup.check ranks candidates.

ARM B (treatment, CMT-lite): a minimal Complementary Memory Trace. For each of
the baseline's top-K hop-1 candidates, build a "trace vector" — the centroid of
that candidate's own top-3 nearest neighbours in the index (a cached, reusable
memory trace of what is complementary/adjacent to it, computed once per index
row and stored, matching the paper's "precomputed trace" framing). Hop-2 re-scores
each hop-1 candidate as `alpha * cosine(query, candidate) + (1-alpha) *
cosine(query, trace(candidate))` and re-ranks. This is the smallest construction
that tests the hypothesis: does routing the query through a second-hop trace
change which candidate wins, and does it win more often than it loses.
"""
import json, math, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLE_PATH = Path('/home/lumen/foreman/pilots/0eed2f5bdb92/sample.json')
SAMPLE = json.loads(SAMPLE_PATH.read_text())
QUERIES, INDEX = SAMPLE['queries'], SAMPLE['index']

TOP_K_HOP1 = 5     # candidates carried from hop 1 into hop 2 rerank
TOP_N_TRACE = 3    # neighbours averaged into each candidate's trace vector
ALPHA = 0.7        # weight on the direct query-candidate score vs the trace score


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def norm(a):
    return math.sqrt(sum(x * x for x in a))


def cosine(a, b, na=None, nb=None):
    na = na if na is not None else norm(a)
    nb = nb if nb is not None else norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return dot(a, b) / (na * nb)


def build_traces(index):
    """Precompute each index row's trace vector = centroid of its own top-N
    nearest neighbours in the index (excluding itself). Cached once, reused
    across all 50 queries — this is the "complementary memory trace" store."""
    vecs = [row['vec'] for row in index]
    norms = [norm(v) for v in vecs]
    n = len(index)
    traces = [None] * n
    for i in range(n):
        sims = []
        for j in range(n):
            if i == j:
                continue
            sims.append((cosine(vecs[i], vecs[j], norms[i], norms[j]), j))
        sims.sort(reverse=True)
        top = sims[:TOP_N_TRACE]
        dim = len(vecs[i])
        centroid = [0.0] * dim
        for _, j in top:
            vj = vecs[j]
            for d in range(dim):
                centroid[d] += vj[d]
        k = len(top)
        if k:
            centroid = [c / k for c in centroid]
        traces[i] = centroid
    return traces


def score_query(qvec, index, index_norms):
    sims = []
    for i, row in enumerate(index):
        s = cosine(qvec, row['vec'], nb=index_norms[i])
        sims.append((s, i))
    sims.sort(reverse=True)
    return sims


def run():
    t0 = time.time()
    index_vecs = [row['vec'] for row in INDEX]
    index_norms = [norm(v) for v in index_vecs]

    t_trace0 = time.time()
    traces = build_traces(INDEX)
    trace_norms = [norm(t) for t in traces]
    trace_build_s = time.time() - t_trace0

    per_query = []
    hits_a = hits_b = 0
    t_score0 = time.time()
    for q in QUERIES:
        qvec = q['vec']
        qnorm = norm(qvec)
        sims = score_query(qvec, INDEX, index_norms)

        # Arm A: single-hop, top-1 by direct cosine.
        top1_a_idx = sims[0][1]
        hit_a = INDEX[top1_a_idx]['proposal_id'] == q['parent_id']

        # Arm B: hop-2 rerank of the top-K hop-1 candidates using each
        # candidate's trace vector.
        hop1 = sims[:TOP_K_HOP1]
        rescored = []
        for direct_score, idx in hop1:
            trace_score = cosine(qvec, traces[idx], qnorm, trace_norms[idx])
            combined = ALPHA * direct_score + (1 - ALPHA) * trace_score
            rescored.append((combined, idx))
        rescored.sort(reverse=True)
        top1_b_idx = rescored[0][1]
        hit_b = INDEX[top1_b_idx]['proposal_id'] == q['parent_id']

        hits_a += int(hit_a)
        hits_b += int(hit_b)
        per_query.append({
            'query_id': q['id'],
            'parent_id': q['parent_id'],
            'hit_a': hit_a,
            'hit_b': hit_b,
            'top1_a': INDEX[top1_a_idx]['proposal_id'],
            'top1_b': INDEX[top1_b_idx]['proposal_id'],
        })
    score_s = time.time() - t_score0

    n = len(QUERIES)
    result = {
        'n_queries': n,
        'n_index': len(INDEX),
        'arm_a_r_at_1': hits_a / n,
        'arm_b_r_at_1': hits_b / n,
        'arm_a_hits': hits_a,
        'arm_b_hits': hits_b,
        'trace_build_seconds': trace_build_s,
        'scoring_seconds': score_s,
        'ms_per_query_a_plus_b': (score_s / n) * 1000,
        'top_k_hop1': TOP_K_HOP1,
        'top_n_trace': TOP_N_TRACE,
        'alpha': ALPHA,
        'per_query': per_query,
        'total_seconds': time.time() - t0,
    }
    out = HERE / 'results.json'
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'per_query'}, indent=2))


if __name__ == '__main__':
    run()
