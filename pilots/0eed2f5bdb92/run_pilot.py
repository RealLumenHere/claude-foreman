#!/usr/bin/env python3
"""wq#1559 pilot 0eed2f5bdb92 — knowledge-graph memory vs pgvector+rerank.

Runs on hathor. Pure stdlib: no numpy, no model, no network. Both arms read the
SAME sample.json and are scored by the same function, so the only difference
between them is the retrieval method.

TASK: proposal dedup. For each of the 50 proposals the live gate suppressed as a
duplicate, retrieve the parent it was suppressed against, out of the 605-row dedup
index. The label was written by production at suppression time.

ARM A (baseline, what Lumen does today): cosine over the stored nomic-embed-text
vectors, then the entity-share gate — the pgvector+rerank path as minerva_dedup.check
actually implements it.

ARM B (treatment, the proposal): a writable knowledge graph in SQLite — an adjacency
table of (proposal <-> entity) edges, IDF-weighted, scored by weighted shared-entity
overlap. This is CGM-Lite's semantic-triple store reduced to the smallest thing that
can answer the question.

ARM C: fusion of A and B, because the proposal says COMPLEMENT pgvector, not replace
it. Reporting only A vs B would answer a question the proposal did not ask.
"""
import json, math, re, sqlite3, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLE = json.loads((HERE / 'sample.json').read_text())
QUERIES, INDEX = SAMPLE['queries'], SAMPLE['index']
ENTITY_SHARE = SAMPLE['entity_share']
GRAPH_DB = HERE / 'graph.db'

STOP = set('''add adds adding integrate integrating integration adopt adopting evaluate evaluating
litellm gateway route routes model models modeling pool endpoint endpoints local remote inference
inferencing api apis config configuration yaml service services lumen minerva fleet agent agents
gguf fp8 int8 q4 q8 awq quantized quantization support via into for the and with as a an of on to
new low high cost costs latency throughput coding code research researcher tasks task diversified
diversify diversification fallback provider primary secondary reasoning vision embedding embeddings
scout intel pipeline pipelines use using benchmark benchmarking test testing eval evaluation'''.split())


def entities(text):
    out = set()
    for w in re.split(r'[^a-z0-9.]+', (text or '').lower()):
        w = w.strip('.')
        if not w or w in STOP:
            continue
        if any(ch.isdigit() for ch in w) or len(w) >= 4:
            out.add(w)
    return out


def entity_share(a, b):
    A, B = entities(a), entities(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def cos(a, b):
    dot = sa = sb = 0.0
    for x, y in zip(a, b):
        dot += x * y; sa += x * x; sb += y * y
    if sa <= 0 or sb <= 0:
        return 0.0
    return dot / (math.sqrt(sa) * math.sqrt(sb))


# ── ARM B: build the writable graph ──────────────────────────────────────────
def build_graph():
    if GRAPH_DB.exists():
        GRAPH_DB.unlink()
    c = sqlite3.connect(GRAPH_DB)
    c.execute('CREATE TABLE edges (src TEXT, dst TEXT, w REAL)')     # proposal -> entity
    c.execute('CREATE INDEX ix_src ON edges(src)')
    c.execute('CREATE INDEX ix_dst ON edges(dst)')
    df = {}
    rows = []
    for it in INDEX:
        for e in entities(it['title']):
            rows.append((it['proposal_id'], e))
            df[e] = df.get(e, 0) + 1
    n = max(1, len(INDEX))
    c.executemany('INSERT INTO edges VALUES (?,?,?)',
                  [(s, d, math.log(1 + n / df[d])) for s, d in rows])
    c.commit()
    return c, df, n


def graph_scores(conn, df, n, qtext):
    """Adjacency lookup: query entities -> neighbouring proposals, IDF-weighted."""
    qe = entities(qtext)
    if not qe:
        return {}
    marks = ','.join('?' * len(qe))
    out = {}
    for src, w in conn.execute(
            f'SELECT src, SUM(w) FROM edges WHERE dst IN ({marks}) GROUP BY src', tuple(qe)):
        out[src] = w
    norm = sum(math.log(1 + n / df[e]) for e in qe if e in df) or 1.0
    return {k: v / norm for k, v in out.items()}


# ── scoring ──────────────────────────────────────────────────────────────────
def score_arm(name, rank_fn):
    t0 = time.time()
    r1 = r5 = 0
    mrr = 0.0
    misses = []
    for q in QUERIES:
        ranked = rank_fn(q)
        ids = [pid for pid, _ in ranked]
        if q['parent_id'] in ids:
            pos = ids.index(q['parent_id']) + 1
            mrr += 1.0 / pos
            if pos == 1:
                r1 += 1
            if pos <= 5:
                r5 += 1
            else:
                misses.append((q['id'], pos))
        else:
            misses.append((q['id'], None))
    n = len(QUERIES)
    ms = (time.time() - t0) * 1000 / n
    res = {'arm': name, 'n': n, 'R@1': round(r1 / n, 3), 'R@5': round(r5 / n, 3),
           'MRR': round(mrr / n, 3), 'ms_per_query': round(ms, 1),
           'misses_at5': len(misses)}
    print(f"{name:<28} R@1={res['R@1']:<6} R@5={res['R@5']:<6} MRR={res['MRR']:<6} "
          f"{res['ms_per_query']}ms/q")
    return res, misses


def main():
    print(f'sample: {len(QUERIES)} labeled queries, {len(INDEX)} index rows')
    conn, df, n = build_graph()
    edges = conn.execute('SELECT count(*) FROM edges').fetchone()[0]
    ents = conn.execute('SELECT count(DISTINCT dst) FROM edges').fetchone()[0]
    print(f'graph: {edges} edges over {ents} entities, {GRAPH_DB.stat().st_size} bytes\n')

    def dense(q):
        s = [(it['proposal_id'], cos(q['vec'], it['vec'])) for it in INDEX
             if it['proposal_id'] != q['id']]
        return sorted(s, key=lambda x: -x[1])

    def arm_a(q):
        """Baseline: dense, then the live entity-share gate as the rerank stage."""
        out = []
        for pid, sc in dense(q):
            it = next(i for i in INDEX if i['proposal_id'] == pid)
            if entity_share(q['text'], it['title']) >= ENTITY_SHARE:
                out.append((pid, sc))
        return out

    def arm_b(q):
        g = graph_scores(conn, df, n, q['text'])
        g.pop(q['id'], None)
        return sorted(g.items(), key=lambda x: -x[1])

    def arm_c(q):
        d = dict(arm_a(q))
        g = arm_b(q)
        gd = dict(g)
        keys = set(d) | set(gd)
        # equal-weight fusion of the two normalized signals
        return sorted(((k, 0.5 * d.get(k, 0.0) + 0.5 * gd.get(k, 0.0)) for k in keys),
                      key=lambda x: -x[1])

    results = []
    for nm, fn in (('A baseline dense+rerank', arm_a),
                   ('B graph only', arm_b),
                   ('C fused (A+B)', arm_c)):
        r, _ = score_arm(nm, fn)
        results.append(r)

    (HERE / 'results.json').write_text(json.dumps(results, indent=2))
    print(f'\nwrote {HERE / "results.json"}')


if __name__ == '__main__':
    main()
