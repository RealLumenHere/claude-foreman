"""cmt_retrieval.py — Complementary Memory Traces retrieval (pilot a7d04e5ebe7d,
wq#1684, wq#1559 defer pipeline).

`recall.py` injects the top-k newest facts into a prompt with no query
conditioning at all — there is nothing in this repo today that retrieves a
fact BECAUSE a query needs it. This module is the pilot's treatment: a
query-conditioned retriever over the same kind of fact store, built two
ways so the pilot can compare them on a fixed multi-hop sample:

- `retrieve_single_trace` — the baseline. One trace per fact: its own text.
  A fact is scored by token overlap with the query, nothing else. This is
  the natural query-conditioned generalization of "match the text" and is
  the ceiling of what a single verbatim trace can do.
- `retrieve_complementary_traces` — the treatment. Each fact keeps its
  verbatim trace AND a second, complementary trace: the salient entities
  in it (capitalized tokens, dotted/IP-shaped tokens, code-identifier-shaped
  tokens, quoted phrases). Retrieval runs two hops: hop 1 is
  `retrieve_single_trace` against the query; hop 2 re-queries using the
  entities pulled from hop 1's own results, matched against every fact's
  entity trace. This lets a second fact join the result set even when it
  shares no words with the ORIGINAL query, only with an entity the first
  hop surfaced — the mechanism the paper's technique targets for multi-hop
  retrieval, cut down to something measurable with no embeddings and no
  external services (stdlib only, matching recall.py's existing posture).

Both functions are pure and take a list of facts, never a store path, so
they can be scored against a pinned sample without touching disk.
"""
import re

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "of", "on", "in", "to",
    "for", "and", "or", "does", "did", "what", "why", "how", "which",
    "at", "actually", "must", "can", "not", "no", "when", "that",
}

# entity-shaped tokens: dotted IPs/paths, ALLCAPS/CamelCase identifiers,
# numbers-with-context, and hyphenated identifiers — anything more specific
# than an ordinary lowercase word.
_ENTITY_RE = re.compile(
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b"       # IPv4
    r"|\b[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b"  # CamelCase / ALLCAPS idents
    r"|\bwq#\d+\b"                        # ticket ids
    r"|\b[a-zA-Z_./-]+\.[a-zA-Z]{2,4}\b"  # file-ish paths
    r"|\bflag \d+\b"
)


def _tokens(text):
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def _entities(text):
    return {m.strip() for m in _ENTITY_RE.findall(text)}


def _score(query_tokens, fact_tokens):
    if not query_tokens or not fact_tokens:
        return 0.0
    overlap = query_tokens & fact_tokens
    return len(overlap) / len(query_tokens)


def retrieve_single_trace(facts, query, k):
    """Baseline: rank facts by verbatim-token overlap with the query."""
    q_tokens = _tokens(query)
    scored = [
        (fact["id"], _score(q_tokens, _tokens(fact["text"])))
        for fact in facts
    ]
    scored = [s for s in scored if s[1] > 0]
    scored.sort(key=lambda s: (-s[1], s[0]))
    return [fid for fid, _ in scored[:k]]


def retrieve_complementary_traces(facts, query, k, hop1_k=2):
    """Treatment: hop 1 is retrieve_single_trace; hop 2 bridges via the
    entity trace of whatever hop 1 returned. Results are unioned, hop 1
    ordered first (it answered the literal query), hop 2 appended after,
    deduped, capped at k."""
    by_id = {fact["id"]: fact for fact in facts}
    hop1_ids = retrieve_single_trace(facts, query, hop1_k)

    bridge_entities = set()
    for fid in hop1_ids:
        bridge_entities |= _entities(by_id[fid]["text"])

    hop2_scored = []
    if bridge_entities:
        for fact in facts:
            if fact["id"] in hop1_ids:
                continue
            fact_entities = _entities(fact["text"])
            overlap = bridge_entities & fact_entities
            if overlap:
                hop2_scored.append((fact["id"], len(overlap)))
    hop2_scored.sort(key=lambda s: (-s[1], s[0]))
    hop2_ids = [fid for fid, _ in hop2_scored]

    out = []
    for fid in hop1_ids + hop2_ids:
        if fid not in out:
            out.append(fid)
        if len(out) >= k:
            break
    return out[:k]
