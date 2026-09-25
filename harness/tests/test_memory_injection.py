"""wq#1183 fworker <-> memory/ integration, zero quota: distilled-fact
injection into the worker prompt (fail-open) and the episodic append helper
(fail-open). No claude invocation."""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

TICKET = {"title": "t", "detail": "d", "lane": "infra"}

# wq#1635 retired the distilled-fact recall source (memory/recall.py -> memory/attic/,
# cd35dab): the fact store was never built, so it only ever contributed an empty block
# (fworker.distilled_memory_block's own docstring). The wq#1183 tests that seeded a fact
# store and monkeypatched recall.DEFAULT_STORE/top_facts are gone with it — their premise
# (fworker imports recall at prompt-build time) is obsolete, not merely failing.


def test_prompt_fail_open_no_memory_block(monkeypatch):
    """With no fleet-context state to inject, the slot vanishes and the prompt still
    carries the ticket — the wq#1183 fail-open posture, minus the retired recall source."""
    fleet_context = fworker._prompt_forge_import("fleet_context")
    monkeypatch.setattr(fleet_context, "ticket_state_block", lambda ticket_id: "")
    prompt = fworker.load_worker_prompt(5, TICKET, "/tmp/wt")
    assert "{{DISTILLED_MEMORY}}" not in prompt
    assert "DISTILLED FLEET MEMORY" not in prompt
    assert "YOUR TICKET: wq#5" in prompt


def test_prompt_injects_ticket_subject_state(monkeypatch):
    """wq#1636: subject-scoped lumen_state facts for the ticket ride the
    {{DISTILLED_MEMORY}} slot."""
    fleet_context = fworker._prompt_forge_import("fleet_context")
    monkeypatch.setattr(fleet_context, "ticket_state_block",
                         lambda ticket_id: f"Subject-scoped state for wq#{ticket_id}:\n"
                                            "- anubis.role = Borg backup repo (since 2026-06-01)\n")
    prompt = fworker.load_worker_prompt(5, TICKET, "/tmp/wt")
    assert "{{DISTILLED_MEMORY}}" not in prompt
    assert "Subject-scoped state for wq#5" in prompt
    assert "anubis.role = Borg backup repo" in prompt


def test_prompt_fail_open_on_fleet_context_crash(monkeypatch):
    fleet_context = fworker._prompt_forge_import("fleet_context")
    def boom(ticket_id):
        raise RuntimeError("minerva unreachable")
    monkeypatch.setattr(fleet_context, "ticket_state_block", boom)
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
