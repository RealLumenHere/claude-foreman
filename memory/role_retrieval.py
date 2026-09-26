"""role_retrieval.py — memory ROLE separation retrieval (pilot edd69c525413,
wq#1935, wq#1559 defer pipeline).

Today's retrieval (`cmt_retrieval.retrieve_single_trace`) scores every fact
against a query by verbatim token overlap alone — a `git status` habit and a
"deploys freeze after 5pm" policy fact compete for the same top-k slots with
no notion that one is a one-off EVENT and the other is a standing RULE. This
module is the pilot's treatment: classify each fact into one of three roles
(episodic/semantic/procedural) with a deterministic keyword/pattern
classifier (no embeddings, no LLM call — matches this repo's stdlib-only
memory convention, see `cmt_retrieval.py`), then let retrieval optionally
filter to a caller-specified role before ranking by the existing token-overlap
score. Facts and scoring reuse `cmt_retrieval`'s tokenizer so the two arms of
the pilot differ only in the role filter, not in the underlying match logic.

- `classify_role(text)` — the treatment's classifier. Scores a fact's text
  against three keyword/pattern sets (episodic: past-tense event markers,
  timestamps, "ran"/"observed"/"attempt"; procedural: imperative/step markers,
  "run:", "step", "always"/"never"; semantic: definitional markers, "is a",
  "means", "the capital of") and returns the highest-scoring role, defaulting
  to "semantic" on a tie or no match (a bare fact with no role signal is
  closest to a context-free semantic statement).
- `retrieve_with_role_filter` — the treatment retriever. When `role` is given,
  facts are filtered to `classify_role(fact["text"]) == role` before ranking;
  `role=None` degrades to plain `cmt_retrieval.retrieve_single_trace` (the
  baseline), so the treatment is a strict superset of the baseline's behavior.

Pure functions over a list of facts, never a store path, so they can be
scored against a pinned sample without touching disk — same posture as
`cmt_retrieval.py`.
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import cmt_retrieval  # noqa: E402

ROLES = ("episodic", "semantic", "procedural")

_EPISODIC_RE = re.compile(
    r"\bran\b|\bobserved\b|\battempt\b|\btoday\b|\byesterday\b"
    r"|\b20\d{2}-\d{2}-\d{2}\b|\bat \d{1,2}:\d{2}\b|\bwq#\d+\b|\bhappened\b"
    r"|\bfailed\b|\bcrashed\b|\bfound that\b", re.IGNORECASE)

_PROCEDURAL_RE = re.compile(
    r"\bstep \d\b|\brun:\b|\bto fix\b|\balways\b|\bnever\b|\bmust\b"
    r"|\buse \`|\bfirst,? |\bthen,? |\bdo not\b|\bbefore you\b", re.IGNORECASE)

_SEMANTIC_RE = re.compile(
    r"\bis a\b|\bis the\b|\bmeans\b|\bdefined as\b|\brefers to\b"
    r"|\bthe capital of\b|\bcanonical\b|\bstands for\b", re.IGNORECASE)


def classify_role(text):
    """Deterministic keyword/pattern classifier -> one of ROLES."""
    scores = {
        "episodic": len(_EPISODIC_RE.findall(text)),
        "procedural": len(_PROCEDURAL_RE.findall(text)),
        "semantic": len(_SEMANTIC_RE.findall(text)),
    }
    best_role = max(ROLES, key=lambda r: scores[r])
    if scores[best_role] == 0:
        return "semantic"
    return best_role


def retrieve_with_role_filter(facts, query, k, role=None):
    """Treatment: rank by cmt_retrieval's token-overlap score, restricted to
    facts whose classify_role() matches `role` (role=None = no filter, i.e.
    identical to cmt_retrieval.retrieve_single_trace — the baseline)."""
    if role is None:
        return cmt_retrieval.retrieve_single_trace(facts, query, k)
    scoped = [f for f in facts if classify_role(f["text"]) == role]
    return cmt_retrieval.retrieve_single_trace(scoped, query, k)
