"""Layer 2 integration (wq#985): run_child() must SIGTERM a child that is
looping (3x same-class failure) with killed_reason='loop_break', while a
healthy child runs to completion untouched. Uses a real subprocess emitting
stream-json frames — the same plumbing a live `claude -p` child rides."""
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import action_gate
import fworker


def make_child(tmp_path, looping):
    """A child that emits NDJSON frames: init, then N (tool_use -> failed
    tool_result) rounds, then (if not killed) a result frame; then sleeps so
    only a kill ends it early."""
    rounds = 5 if looping else 2
    err = ("adb: no devices found" if looping else "transient {i}")
    script = textwrap.dedent(f"""\
        import json, sys, time
        def emit(f):
            print(json.dumps(f), flush=True)
        emit({{"type": "system", "subtype": "init", "session_id": "loop-int",
              "model": "fake"}})
        for i in range({rounds}):
            emit({{"type": "assistant", "message": {{"content": [
                {{"type": "tool_use", "id": f"t{{i}}", "name": "Bash",
                  "input": {{"command": "adb wait-for-device"}}}}]}}}})
            emit({{"type": "user", "message": {{"content": [
                {{"type": "tool_result", "tool_use_id": f"t{{i}}", "is_error": True,
                  "content": [{{"type": "text", "text": f"{err}"}}]}}]}}}})
            time.sleep(0.05)
        emit({{"type": "result", "subtype": "success", "is_error": False}})
        time.sleep(0.5)
    """)
    p = tmp_path / ("loop_child.py" if looping else "ok_child.py")
    p.write_text(script)
    return [sys.executable, str(p)]


def run(tmp_path, looping):
    tracker = action_gate.LoopTracker(threshold=3)
    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child(
        make_child(tmp_path, looping), str(tmp_path), dict(os.environ),
        max_turns=50, wall_clock_s=30, ticket_id=999999, attempt=1,
        frame_gap_s=10, post_result_grace_s=2, loop_tracker=tracker)
    return tracker, killed_reason, exit_code


def test_looping_child_is_stopped(tmp_path):
    tracker, killed_reason, exit_code = run(tmp_path, looping=True)
    assert killed_reason == "loop_break"
    assert tracker.tripped and "adb" in tracker.tripped
    # SIGTERM'd: normalized exit is 128+15 (or the child died before wait)
    assert exit_code != 0


def test_healthy_child_untouched(tmp_path):
    tracker, killed_reason, exit_code = run(tmp_path, looping=False)
    assert killed_reason in (None, "post_result_stuck")
    assert tracker.tripped is None
