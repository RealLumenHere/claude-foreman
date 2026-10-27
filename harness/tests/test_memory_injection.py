"""wq#1183 fworker <-> memory/ integration, zero quota: distilled-fact
injection into the worker prompt (fail-open) and the episodic append helper
(fail-open). No claude invocation."""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

TICKET = {"title": "t", "detail": "d", "lane": "infra"}

# wq#3222: the wq#1183 distilled-fact recall tests went with the source they covered —
# wq#1635 retired it and cd35dab2 moved memory/recall.py to memory/attic (its own tests
# live there). {{DISTILLED_MEMORY}} now carries only fleet_context's subject state.


def test_research_synthesis_prompt_carries_web_recipe(monkeypatch):
    # wq#4716: the agents.yaml prompt_addendum renders only for its own archetype.
    fleet_context = fworker._prompt_forge_import("fleet_context")
    monkeypatch.setattr(fleet_context, "ticket_state_block", lambda ticket_id: "")
    research = fworker.load_worker_prompt(5, TICKET, "/tmp/wt", archetype="research-synthesis")
    plain = fworker.load_worker_prompt(5, TICKET, "/tmp/wt", archetype="standard-engineering")
    legacy = fworker.load_worker_prompt(5, TICKET, "/tmp/wt")
    assert "WEB RESEARCH RECIPE" in research and "fleet-search web" in research
    assert research.index("WEB RESEARCH RECIPE") < research.index("YOUR TICKET: wq#5")
    for p in (research, plain, legacy):
        assert "{{ARCHETYPE_ADDENDUM}}" not in p
    assert "WEB RESEARCH RECIPE" not in plain and "WEB RESEARCH RECIPE" not in legacy


def test_prompt_fail_open_empty_state(monkeypatch):
    fleet_context = fworker._prompt_forge_import("fleet_context")
    monkeypatch.setattr(fleet_context, "ticket_state_block", lambda ticket_id: "")
    prompt = fworker.load_worker_prompt(5, TICKET, "/tmp/wt")
    assert "{{DISTILLED_MEMORY}}" not in prompt
    assert "Subject-scoped state" not in prompt
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
