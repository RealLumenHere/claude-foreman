"""recall.py unit tests (wq#1183) — newest-first ordering, scope filter, dedup,
k cap, fail-open on missing/broken store, render_block contract."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recall  # noqa: E402


def _note(store, name, lines):
    d = os.path.join(store, "facts")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w") as f:
        f.write("---\nsource: test\n---\n\n## Facts\n" + "\n".join(lines) + "\n")


def test_newest_note_first(tmp_path):
    _note(str(tmp_path), "2026-08-01-wq1.md", ["- [fleet] old fact"])
    _note(str(tmp_path), "2026-09-01-wq2.md", ["- [fleet] new fact"])
    facts = recall.top_facts(store=str(tmp_path))
    assert facts[0] == ("fleet", "new fact")
    assert facts[1] == ("fleet", "old fact")


def test_scope_filter(tmp_path):
    _note(str(tmp_path), "2026-09-01-wq1.md", [
        "- [fleet] shared fact",
        "- [repo:claude-foreman] repo fact",
        "- [repo:other] other repo fact",
    ])
    facts = recall.top_facts(scopes={"fleet", "repo:claude-foreman"},
                             store=str(tmp_path))
    assert ("fleet", "shared fact") in facts
    assert ("repo:claude-foreman", "repo fact") in facts
    assert all(s != "repo:other" for s, _ in facts)


def test_dedup_and_confidence_suffix_stripped(tmp_path):
    _note(str(tmp_path), "2026-09-01-wq1.md",
          ["- [fleet] Same Fact (confidence 0.90)"])
    _note(str(tmp_path), "2026-08-01-wq2.md",
          ["- [fleet] same fact (confidence 0.50)"])
    facts = recall.top_facts(store=str(tmp_path))
    assert facts == [("fleet", "Same Fact")]


def test_k_cap(tmp_path):
    _note(str(tmp_path), "2026-09-01-wq1.md",
          [f"- [fleet] fact {i}" for i in range(20)])
    assert len(recall.top_facts(k=3, store=str(tmp_path))) == 3


def test_missing_store_is_empty(tmp_path):
    assert recall.top_facts(store=str(tmp_path / "nope")) == []


def test_non_fact_lines_ignored(tmp_path):
    _note(str(tmp_path), "2026-09-01-wq1.md", [
        "- [fleet] real fact",
        "not a bullet",
        "- no scope bullet",
        "## Proposed lumen_state ingests",
        "- `gw.role = gateway`",
    ])
    assert recall.top_facts(store=str(tmp_path)) == [("fleet", "real fact")]


def test_render_block(tmp_path):
    assert recall.render_block([]) == ""
    block = recall.render_block([("fleet", "a fact")])
    assert block.startswith(recall.BLOCK_HEADER)
    assert "- [fleet] a fact" in block
