#!/usr/bin/env python3
"""wq#1560 pilot 0eed2f5bdb92 — is the arm A / arm C gap real, or is it one query?

run_pilot.py reports aggregates only. At n=50 one query moves R@1 by 0.02, so the
observed A-vs-C gap is unreadable without a paired test over the SAME queries.

PRIMARY (pre-registered on the card): exact paired McNemar on per-query rank-1
hit/miss. Discordant pairs only — b = A hit & C missed, c = C hit & A missed —
two-sided exact binomial on min(b,c) against b+c trials at p=0.5.

SECONDARY, POST-HOC, NOT DECISIVE: graph-only recall@k. Written after the primary
numbers were seen; it is reported as an open question, never as the win condition.

Guard against drift: the arms are rebuilt here from run_pilot's own module-level
helpers and the aggregates are asserted equal to results.json. If this file ever
ranks differently from the file that produced the headline numbers, it fails loudly
instead of quietly reporting a different experiment.
"""
import json
import sys
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_pilot as R  # noqa: E402  (module-level helpers only; main() is guarded)

QUERIES, INDEX, ENTITY_SHARE = R.QUERIES, R.INDEX, R.ENTITY_SHARE
BY_ID = {it['proposal_id']: it for it in INDEX}


def dense(q):
    s = [(pid, R.cos(q['vec'], it['vec'])) for pid, it in BY_ID.items() if pid != q['id']]
    return sorted(s, key=lambda x: -x[1])


def arm_a(q):
    return [(pid, sc) for pid, sc in dense(q)
            if R.entity_share(q['text'], BY_ID[pid]['title']) >= ENTITY_SHARE]


def arm_b(q, conn, df, n):
    g = R.graph_scores(conn, df, n, q['text'])
    g.pop(q['id'], None)
    return sorted(g.items(), key=lambda x: -x[1])


def arm_c(a, b):
    d, gd = dict(a), dict(b)
    return sorted(((k, 0.5 * d.get(k, 0.0) + 0.5 * gd.get(k, 0.0)) for k in set(d) | set(gd)),
                  key=lambda x: -x[1])


def rank_of(ranked, parent_id):
    ids = [pid for pid, _ in ranked]
    return ids.index(parent_id) + 1 if parent_id in ids else None


def aggregates(ranks):
    n = len(ranks)
    return {'R@1': round(sum(r == 1 for r in ranks) / n, 3),
            'R@5': round(sum(r is not None and r <= 5 for r in ranks) / n, 3),
            'MRR': round(sum(1.0 / r for r in ranks if r) / n, 3)}


def mcnemar_exact(hits_x, hits_y):
    """Two-sided exact McNemar. b/c are the discordant counts; concordant pairs
    carry no information about which arm is better and are excluded by construction."""
    b = sum(1 for x, y in zip(hits_x, hits_y) if x and not y)
    c = sum(1 for x, y in zip(hits_x, hits_y) if y and not x)
    n = b + c
    if n == 0:
        return b, c, 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return b, c, min(1.0, 2 * tail)


def main():
    conn, df, n = R.build_graph()
    ranks = {'A': [], 'B': [], 'C': []}
    for q in QUERIES:
        a = arm_a(q)
        b = arm_b(q, conn, df, n)
        for name, ranked in (('A', a), ('B', b), ('C', arm_c(a, b))):
            ranks[name].append(rank_of(ranked, q['parent_id']))

    # drift guard: these arms must be the arms that produced results.json
    published = {r['arm'][0]: r for r in json.loads((HERE / 'results.json').read_text())}
    for name in 'ABC':
        got, want = aggregates(ranks[name]), published[name]
        for k in ('R@1', 'R@5', 'MRR'):
            assert got[k] == want[k], f'arm {name} {k}: significance={got[k]} results.json={want[k]}'
    print('drift guard: arms reproduce results.json aggregates exactly')

    hits = {k: [r == 1 for r in v] for k, v in ranks.items()}
    out = {'n': len(QUERIES), 'aggregates': {k: aggregates(v) for k, v in ranks.items()},
           'ranks': {k: v for k, v in ranks.items()}, 'mcnemar': {}}
    for x, y in (('A', 'C'), ('A', 'B')):
        b, c, p = mcnemar_exact(hits[x], hits[y])
        out['mcnemar'][f'{x}_vs_{y}'] = {f'{x}_only': b, f'{y}_only': c, 'p_two_sided': round(p, 4)}
        print(f'{x} vs {y}: {x}-only-hit={b} {y}-only-hit={c} p={p:.4f}')

    # POST-HOC, secondary: graph-only as a cheap candidate generator.
    for k in (5, 10, 20, 50):
        rec = sum(1 for r in ranks['B'] if r is not None and r <= k) / len(QUERIES)
        out.setdefault('graph_recall_at_k', {})[k] = round(rec, 3)
    print('graph-only recall@k (post-hoc):', out['graph_recall_at_k'])

    (HERE / 'significance.json').write_text(json.dumps(out, indent=2))
    print(f'wrote {HERE / "significance.json"}')


if __name__ == '__main__':
    main()
