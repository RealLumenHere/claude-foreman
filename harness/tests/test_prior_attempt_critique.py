"""wq#2488: the reflexion critique appended to prior-attempt-notes.md between
fworker attempt 1 and 2 (Orchestation 1 L858-913, minus its rewrite_node - this
only improves attempt 2's briefing, never rewrites code). Two contracts:
write_prior_attempt_notes() adds a '## critique' section with the three fixed
headings when the LLM call succeeds, and a raised exception from that call
leaves the pre-existing mechanical sections intact and the function still
returns normally (fail-open, ticket step 4).
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

FAILED_TRANSCRIPT = [
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Running the test suite now."}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
         "content": [{"type": "text", "text": "ModuleNotFoundError: no module named 'foo'"}]}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "The import is missing; I could not fix it in time."}]}},
]


def test_critique_headings_appear_on_a_synthetic_failed_transcript(monkeypatch, tmp_path):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(fworker, "read_litellm_key", lambda: "fake-key")
    monkeypatch.setattr(
        fworker, "_critique_prior_attempt",
        lambda *a, **k: "WHAT FAILED\n- missing import\nWHY\n- dep not installed\n"
                        "DO DIFFERENTLY\n- add the dependency first")

    path = fworker.write_prior_attempt_notes(
        2488, 1, "/nowhere", FAILED_TRANSCRIPT, "HARNESS_DEFECT")

    text = open(path).read()
    assert "## last events (mechanical tail, up to 20)" in text
    assert "## critique" in text
    for heading in ("WHAT FAILED", "WHY", "DO DIFFERENTLY"):
        assert heading in text


def test_critique_failure_leaves_mechanical_notes_intact(monkeypatch, tmp_path):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))

    def _boom(*a, **k):
        raise RuntimeError("gateway unreachable")
    monkeypatch.setattr(fworker, "_critique_prior_attempt", _boom)

    path = fworker.write_prior_attempt_notes(
        2488, 1, "/nowhere", FAILED_TRANSCRIPT, "HARNESS_DEFECT")

    text = open(path).read()
    assert "## critique" not in text
    assert "## last events (mechanical tail, up to 20)" in text
    assert "## git diff --stat (prior worktree)" in text
    assert "classification: HARNESS_DEFECT" in text


def test_critique_only_runs_for_the_attempt_that_gets_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))
    calls = []
    monkeypatch.setattr(fworker, "_critique_prior_attempt",
                         lambda *a, **k: calls.append(1) or "WHAT FAILED\nWHY\nDO DIFFERENTLY")

    fworker.write_prior_attempt_notes(2488, 2, "/nowhere", FAILED_TRANSCRIPT, "HARNESS_DEFECT")

    assert calls == []


def test_critique_prior_attempt_returns_none_without_a_key(monkeypatch):
    monkeypatch.setattr(fworker, "read_litellm_key",
                         lambda: (_ for _ in ()).throw(OSError("no key file")))
    assert fworker._critique_prior_attempt("HARNESS_DEFECT", "", [], []) is None
