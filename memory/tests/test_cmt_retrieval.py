"""cmt_retrieval.py unit tests (pilot a7d04e5ebe7d, wq#1684): baseline
single-trace overlap ranking, and the treatment's hop-2 entity bridge."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cmt_retrieval  # noqa: E402


FACTS = [
    {"id": 0, "text": "hathor's management IP address is 100.80.71.13."},
    {"id": 1, "text": "100.80.71.13 has been powered off since 2026-07-18."},
    {"id": 2, "text": "distill.py extracts at most 5 validated facts per episode."},
    {"id": 3, "text": "recall.py is stdlib-only: no embeddings, no FTS."},
]


def test_single_trace_finds_direct_match():
    out = cmt_retrieval.retrieve_single_trace(FACTS, "what is hathor's IP address", 2)
    assert 0 in out


def test_single_trace_misses_bridge_fact():
    # fact 1 shares no words with the query at all, only the IP number,
    # which is exactly the gap complementary traces are meant to close.
    out = cmt_retrieval.retrieve_single_trace(FACTS, "why is hathor unreachable", 4)
    assert 1 not in out


def test_complementary_traces_bridges_via_entity():
    out = cmt_retrieval.retrieve_complementary_traces(
        FACTS, "why is hathor's management IP address unreachable", 4)
    assert 0 in out
    assert 1 in out


def test_complementary_traces_no_bridge_entity_falls_back_to_hop1():
    out = cmt_retrieval.retrieve_complementary_traces(
        FACTS, "how many facts does distill extract per episode", 4)
    assert 2 in out


def test_retrieve_respects_k():
    out = cmt_retrieval.retrieve_single_trace(FACTS, "facts", 1)
    assert len(out) <= 1


def test_no_match_returns_empty():
    assert cmt_retrieval.retrieve_single_trace(FACTS, "zzz qqq", 4) == []
