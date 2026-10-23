#!/usr/bin/env python3
"""
context_selector.py - The core token-reduction primitive.

Usage:
    from context_selector import select
    reduced_memory = select(user_query, max_tokens=20000)

Returns: str with CORE sections (always) + top-K query-relevant sections,
token budget respected, total < max_tokens.

Retrieval (Memory v2 Phase 0, 2026-06-25): HYBRID.
  - dense: cosine similarity of query vs each section embedding (Ollama embed model)
  - sparse: BM25 over each section's full lowercased content (catches atomic facts:
    IPs, hostnames, exact tokens that a coarse section-level embedding misses)
  - fusion: Reciprocal Rank Fusion (RRF) of dense + BM25 ranks
  - fail-safe: any hybrid error falls back to dense-only (old behaviour); if the
    embed endpoint is down, falls back to BM25/keyword over content.
"""
import json, os, sys, re, base64, requests, math
from pathlib import Path
import numpy as np

SECTIONS_JSON = Path('/home/lumen/minerva/context/sections.json')
SECTIONS_DIR = Path('/home/lumen/minerva/context/sections')
LIVE_STATE = Path('/home/lumen/minerva/context/LIVE_FLEET_STATE.md')  # Memory v2 P1: never-stale fleet slice, injected at top of every render
OLLAMA = 'http://127.0.0.1:11434'
EMBED_MODEL = 'bge-m3'   # set by swap_embedder.sh 20260625_043432
RRF_K = 60
BM25_K1 = 1.5
BM25_B = 0.75
STATE_SOFT_CAP_TOK = 4000   # live-state size above which we WARN (never truncate; prune at gen time)

def embed(text):
    try:
        r = requests.post(f'{OLLAMA}/api/embed',
            json={'model': EMBED_MODEL, 'input': text[:10000]}, timeout=10)
        if r.status_code != 200:
            return None
        vec = r.json().get('embeddings', [[]])[0]
        return np.array(vec, dtype=np.float32) if vec else None
    except Exception:
        return None

def cosine(a, b):
    if a is None or b is None: return 0.0
    d = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
    return float(np.dot(a, b) / d)

def load_sections():
    if not SECTIONS_JSON.exists():
        return []
    return json.loads(SECTIONS_JSON.read_text())

def read_section(sid):
    p = SECTIONS_DIR / f'{sid}.md'
    return p.read_text() if p.exists() else ''

# keep dotted/colon tokens whole so IPs (100.70.94.19) and host:port survive
_TOK = re.compile(r'[a-z0-9]+(?:[._:-][a-z0-9]+)*')
def tokenize(text):
    return _TOK.findall(text.lower())

def _content_lc(s):
    """inline content (fast path) or fall back to the per-section file."""
    c = s.get('content_lc')
    if c is None:
        c = read_section(s['id']).lower()
    return c

def bm25_scores(query, sections):
    """BM25 over section content. Returns {idx: score} aligned to `sections` list order."""
    q_tokens = [t for t in tokenize(query) if len(t) > 1]
    if not q_tokens:
        return {}
    docs = [tokenize(_content_lc(s)) for s in sections]
    N = len(docs) or 1
    avglen = (sum(len(d) for d in docs) / N) or 1.0
    # document frequency per query token
    df = {}
    for qt in set(q_tokens):
        df[qt] = sum(1 for d in docs if qt in d) or 0
    scores = {}
    for i, d in enumerate(docs):
        if not d:
            continue
        dl = len(d)
        s = 0.0
        for qt in q_tokens:
            f = d.count(qt)
            if f == 0:
                continue
            idf = math.log(1 + (N - df[qt] + 0.5) / (df[qt] + 0.5))
            s += idf * (f * (BM25_K1 + 1)) / (f + BM25_K1 * (1 - BM25_B + BM25_B * dl / avglen))
        if s > 0:
            scores[i] = s
    return scores

def _ranked(score_map):
    """idx -> rank (1-based, best=1) for a {idx: score} map."""
    order = sorted(score_map.keys(), key=lambda i: -score_map[i])
    return {idx: r + 1 for r, idx in enumerate(order)}

def _hybrid_rank(user_query, non_core, q_emb, min_similarity):
    """Return list of (fused_score, dense_score, bm25_score, section) for eligible sections, best first."""
    dense = {}
    if q_emb is not None:
        for i, s in enumerate(non_core):
            if s.get('embedding_b64'):
                sec_emb = np.frombuffer(base64.b64decode(s['embedding_b64']), dtype=np.float32)
                dense[i] = cosine(q_emb, sec_emb)
    bm25 = bm25_scores(user_query, non_core)
    dense_rank = _ranked(dense)
    bm25_rank = _ranked(bm25)
    out = []
    for i, s in enumerate(non_core):
        ds = dense.get(i, 0.0)
        bs = bm25.get(i, 0.0)
        # eligibility: real dense signal OR a real query token present in content
        if ds < min_similarity and bs <= 0.0:
            continue
        fused = 0.0
        if i in dense_rank:
            fused += 1.0 / (RRF_K + dense_rank[i])
        if i in bm25_rank:
            fused += 1.0 / (RRF_K + bm25_rank[i])
        out.append((fused, ds, bs, s))
    out.sort(key=lambda x: -x[0])
    return out

def select(user_query, max_tokens=20000, top_k=8, min_similarity=0.35, verbose=False):
    sections = load_sections()
    if not sections:
        return '[context_selector: no section index found - fallback to full memory file]'

    core = [s for s in sections if s.get('core')]
    non_core = [s for s in sections if not s.get('core')]

    q_emb = embed(user_query)
    mode = 'hybrid' if q_emb is not None else 'bm25'

    try:
        ranked = _hybrid_rank(user_query, non_core, q_emb, min_similarity)
        selected_non_core = [s for _f, _d, _b, s in ranked[:max(top_k, 12)]]
    except Exception as e:
        # FAIL-SAFE: revert to original dense-only / title-keyword behaviour
        mode = 'semantic-fallback' if q_emb is not None else 'keyword-fallback'
        scored = []
        for s in non_core:
            if q_emb is not None and s.get('embedding_b64'):
                sec_emb = np.frombuffer(base64.b64decode(s['embedding_b64']), dtype=np.float32)
                sc = cosine(q_emb, sec_emb)
            else:
                qset = set(re.findall(r'\w+', user_query.lower()))
                tset = set(re.findall(r'\w+', s['title'].lower()))
                sc = (len(qset & tset) / max(len(qset), 1)) if qset and tset else 0.0
            if sc >= min_similarity:
                scored.append((sc, s))
        scored.sort(key=lambda x: -x[0])
        selected_non_core = [s for _, s in scored[:top_k]]

    out_parts = []
    used = 0
    header = f'<!-- context_selector: mode={mode}, core={len(core)}, selected={len(selected_non_core)}, budget={max_tokens} -->\n'
    out_parts.append(header)

    # --- hard constraints: live state + core are ALWAYS included, never budget-gated ---
    # (Fix 2026-07-08: a large LIVE_FLEET_STATE slice used to trip the `break` inside the
    # CORE loop and silently evict guaranteed-core context. Core+live are correctness, not
    # budget -- emit unconditionally and fail LOUD if they exceed the budget rather than
    # dropping load-bearing fleet facts. The budget below gates ONLY non-core sections.)
    live = ''
    try:
        if LIVE_STATE.exists():
            live = LIVE_STATE.read_text().strip()
    except Exception:
        live = ''
    live_tok = (len(live) // 4) if live else 0
    core_tok = sum(s['token_estimate'] for s in core)
    base = live_tok + core_tok
    if base > max_tokens:
        out_parts.append(
            f'<!-- WARN context_selector: core+live={base} tok exceeds budget {max_tokens}; '
            f'included anyway (non-evictable). Prune LIVE_FLEET_STATE (state-regen) or raise budget. -->')
    if live_tok > STATE_SOFT_CAP_TOK:
        out_parts.append(
            f'<!-- WARN context_selector: LIVE_FLEET_STATE={live_tok} tok exceeds soft cap '
            f'{STATE_SOFT_CAP_TOK}; prune volatile/decayed facts via state-regen (not truncated here). -->')

    if live:
        out_parts.append(live)
        used += live_tok
    for s in core:
        out_parts.append(read_section(s['id']))
        used += s['token_estimate']

    # --- only non-core retrieved sections are budget-gated ---
    for s in selected_non_core:
        t = s['token_estimate']
        if used + t > max_tokens:
            break
        out_parts.append(read_section(s['id']))
        used += t

    if verbose:
        try:
            dbg = ', '.join(f'{s["title"][:28]}(f={f:.4f},d={d:.2f},b={b:.1f})' for f, d, b, s in ranked[:top_k])
            out_parts.append(f'<!-- ranked: {dbg} -->')
        except Exception:
            pass
    return '\n\n'.join(out_parts)

def rank_only(user_query, top_k=12, min_similarity=0.35):
    """Retrieval-quality probe: ranked non-core sections (title, fused, dense, bm25), ignoring token budget."""
    sections = load_sections()
    non_core = [s for s in sections if not s.get('core')]
    q_emb = embed(user_query)
    ranked = _hybrid_rank(user_query, non_core, q_emb, min_similarity)
    return [(s['title'], f, d, b) for f, d, b, s in ranked[:top_k]]

def stats(user_query, max_tokens=20000):
    sections = load_sections()
    total = sum(s['token_estimate'] for s in sections)
    core = [s for s in sections if s.get('core')]
    core_tokens = sum(s['token_estimate'] for s in core)
    non_core = [s for s in sections if not s.get('core')]
    q_emb = embed(user_query)
    mode = 'hybrid' if q_emb is not None else 'bm25'
    ranked = _hybrid_rank(user_query, non_core, q_emb, 0.35)
    selected = []
    used = core_tokens
    for f, d, b, s in ranked:
        if used + s['token_estimate'] > max_tokens: break
        selected.append((f, s)); used += s['token_estimate']
    return {
        'mode': mode,
        'total_tokens': total,
        'core_tokens': core_tokens,
        'core_overflow': core_tokens > max_tokens,
        'selected_count': len(selected),
        'total_delivered_tokens': used,
        'reduction_pct': 100 * (1 - min(used, total) / max(total, 1)),
        'top_selected': [(float(f), s['title'][:80]) for f, s in selected[:8]],
    }

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: context_selector.py <query> [--stats] [--rank] [--verbose] [--budget N]')
        sys.exit(1)
    args = sys.argv[1:]
    verbose = '--verbose' in args
    stats_only = '--stats' in args
    rank_mode = '--rank' in args
    budget = 20000
    if '--budget' in args:
        budget = int(args[args.index('--budget')+1])
    query = ' '.join([a for a in args if not a.startswith('--') and not a.isdigit()])
    if stats_only:
        import pprint; pprint.pprint(stats(query, max_tokens=budget))
    elif rank_mode:
        for title, f, d, b in rank_only(query):
            print(f'{f:.4f}  d={d:.3f} b={b:5.1f}  {title[:70]}')
    else:
        print(select(query, max_tokens=budget, verbose=verbose))
