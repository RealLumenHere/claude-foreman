"""wq#1183 fworker <-> memory/ integration, zero quota: distilled-fact
injection into the worker prompt (fail-open) and the episodic append helper
(fail-open). No claude invocation."""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

TICKET = {"title": "t", "detail": "d", "lane": "infra"}


def _seed_store(store):
    d = os.path.join(store, "facts")
    os.makedirs(d)
    with open(os.path.join(d, "2026-09-01-wq5.md"), "w") as f:
        f.write("## Facts\n- [fleet] the gateway lives on kepler\n"
                "- [lane:infra] infra lane fact\n"
                "- [repo:other] other repo fact\n")


def test_prompt_injects_scoped_facts(tmp_path, monkeypatch):
    _seed_store(str(tmp_path))
    recall = fworker._memory_import("recall")
    monkeypatch.setattr(recall, "DEFAULT_STORE", str(tmp_path))
    prompt = fworker.load_worker_prompt(5, TICKET, "/tmp/wt")
    assert "{{DISTILLED_MEMORY}}" not in prompt
    assert "DISTILLED FLEET MEMORY" in prompt
    assert "the gateway lives on kepler" in prompt
    assert "infra lane fact" in prompt          # lane scope included
    assert "other repo fact" not in prompt      # unrelated repo excluded


def test_prompt_fail_open_empty_store(tmp_path, monkeypatch):
    recall = fworker._memory_import("recall")
    monkeypatch.setattr(recall, "DEFAULT_STORE", str(tmp_path / "missing"))
    prompt = fworker.load_worker_prompt(5, TICKET, "/tmp/wt")
    assert "{{DISTILLED_MEMORY}}" not in prompt
    assert "DISTILLED FLEET MEMORY" not in prompt
    assert "YOUR TICKET: wq#5" in prompt


def test_prompt_fail_open_on_recall_crash(monkeypatch):
    recall = fworker._memory_import("recall")
    def boom(**kw):
        raise RuntimeError("store on fire")
    monkeypatch.setattr(recall, "top_facts", boom)
    prompt = fworker.load_worker_prompt(5, TICKET, "/tmp/wt")
    assert "{{DISTILLED_MEMORY}}" not in prompt
    assert "YOUR TICKET: wq#5" in prompt


def test_append_episode_event_writes_log(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    fworker.append_episode_event(11, {"type": "attempt_end", "attempt": 1})
    episodic = fworker._memory_import("episodic")
    events = episodic.read_events(11, runs_root=str(tmp_path))
    assert len(events) == 1 and events[0]["type"] == "attempt_end"


def test_append_episode_event_fail_open(tmp_path, monkeypatch):
    # RUNS_ROOT pointing at an unwritable path must log, not raise
    monkeypatch.setattr(fworker, "RUNS_ROOT", "/proc/definitely-unwritable")
    fworker.append_episode_event(11, {"type": "attempt_end"})


def test_assistant_text_tail_extracts_text_blocks():
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "hmm"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "first"}]}},
        {"type": "system"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "second"}]}},
    ]
    assert fworker.assistant_text_tail(events) == ["first", "second"]
    assert fworker.assistant_text_tail(events, n=1) == ["second"]
