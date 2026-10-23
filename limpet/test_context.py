"""Unit tests for context.py: the wq#4508 context-entry assembly over
result_store.StoredResult. Pure, offline."""
import result_store as rs
from context import entry_for


def test_hot_band_entry_has_no_evidence_path(tmp_path):
    stored = rs.store_result(str(tmp_path), "nmap", "short output")
    entry = entry_for("nmap", stored)
    assert entry.text == "short output"
    assert entry.evidence_path is None


def test_warm_band_entry_carries_summary_and_cold_reference(tmp_path):
    big = "x" * (rs.HOT_THRESHOLD + 1)
    stored = rs.store_result(str(tmp_path), "nmap", big, summary="three line summary")
    entry = entry_for("nmap", stored)
    assert entry.text.startswith("three line summary")
    assert stored.cold_path in entry.text
    assert stored.content_hash in entry.text
    assert entry.evidence_path == stored.cold_path
    # the full blob must never land in the context entry (docs/agent-chat-spec.md:144-147)
    assert big not in entry.text
