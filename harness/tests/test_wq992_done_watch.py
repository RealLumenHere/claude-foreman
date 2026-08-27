"""wq#992-C integration: run_child() must stop a child that keeps taking agentic turns
after its ticket has already gone `done`, with killed_reason='ticket_done' — the case
post_result_grace never arms for (no result frame) and the turn-backstop would otherwise
misclassify as TURNS. A child whose ticket is NOT done is left to the ordinary watchdogs.

Uses a real subprocess emitting stream-json frames (same plumbing as a live `claude -p`),
with fworker.wq_show monkeypatched to control the ticket's apparent status and the
grace/poll windows shrunk so the test runs in ~2s.
"""
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402


def _keep_going_child(tmp_path):
    """init, then many tool rounds and NO result frame, then a long sleep — only a kill
    ends it early. Models the agent that ran `wq done` but keeps looping."""
    script = textwrap.dedent("""\
        import json, sys, time
        def emit(f): print(json.dumps(f), flush=True)
        emit({"type": "system", "subtype": "init", "session_id": "done-int",
              "model": "fake"})
        for i in range(80):
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash",
                 "input": {"command": "echo still going"}}]}})
            emit({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": False,
                 "content": [{"type": "text", "text": "ok"}]}]}})
            time.sleep(0.1)
        time.sleep(10)
    """)
    p = tmp_path / "keep_going_child.py"
    p.write_text(script)
    return [sys.executable, str(p)]


def _healthy_child(tmp_path):
    """init, 2 rounds, a real result frame, brief sleep — the ordinary happy path."""
    script = textwrap.dedent("""\
        import json, sys, time
        def emit(f): print(json.dumps(f), flush=True)
        emit({"type": "system", "subtype": "init", "session_id": "ok-int",
              "model": "fake"})
        for i in range(2):
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash",
                 "input": {"command": "echo hi"}}]}})
            time.sleep(0.05)
        emit({"type": "result", "subtype": "success", "is_error": False})
        time.sleep(0.3)
    """)
    p = tmp_path / "healthy_child.py"
    p.write_text(script)
    return [sys.executable, str(p)]


def _run(tmp_path, cmd):
    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child(
        cmd, str(tmp_path), dict(os.environ),
        max_turns=200, wall_clock_s=30, ticket_id=999992, attempt=1,
        frame_gap_s=10, post_result_grace_s=3, loop_tracker=None)
    return killed_reason, exit_code


def test_lingering_after_done_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"status": "done"})
    monkeypatch.setattr(fworker, "DONE_POLL_INTERVAL_S", 0.3)
    monkeypatch.setattr(fworker, "POST_DONE_GRACE_S", 1.0)
    killed_reason, exit_code = _run(tmp_path, _keep_going_child(tmp_path))
    assert killed_reason == "ticket_done"
    assert exit_code != 0  # SIGTERM'd, normalized to 128+signum


def test_not_done_ticket_is_untouched(tmp_path, monkeypatch):
    # ticket still claimed: the done-watch must never arm; ordinary paths handle it.
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"status": "claimed"})
    monkeypatch.setattr(fworker, "DONE_POLL_INTERVAL_S", 0.3)
    monkeypatch.setattr(fworker, "POST_DONE_GRACE_S", 1.0)
    killed_reason, exit_code = _run(tmp_path, _healthy_child(tmp_path))
    assert killed_reason in (None, "post_result_stuck")
    assert killed_reason != "ticket_done"
