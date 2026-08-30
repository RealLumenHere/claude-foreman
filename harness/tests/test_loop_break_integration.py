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
import classifier
import fworker


def make_child(tmp_path, looping, err=None):
    """A child that emits NDJSON frames: init, then N (tool_use -> failed
    tool_result) rounds, then (if not killed) a result frame; then sleeps so
    only a kill ends it early."""
    rounds = 5 if looping else 2
    err = err or ("adb: no devices found" if looping else "transient {i}")
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


def run(tmp_path, looping, err=None):
    tracker = action_gate.LoopTracker(threshold=3)
    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child(
        make_child(tmp_path, looping, err), str(tmp_path), dict(os.environ),
        max_turns=50, wall_clock_s=30, ticket_id=999999, attempt=1,
        frame_gap_s=10, post_result_grace_s=2, loop_tracker=tracker)
    return tracker, killed_reason, exit_code


def test_looping_child_is_stopped(tmp_path):
    tracker, killed_reason, exit_code = run(tmp_path, looping=True)
    assert killed_reason == "loop_break"
    assert tracker.tripped and "adb" in tracker.tripped
    # SIGTERM'd: normalized exit is 128+15 (or the child died before wait)
    assert exit_code != 0


def test_permission_denial_loop_lands_on_permission_loop_not_infra_crash(tmp_path):
    """wq#1107 end-to-end over the real stream-json plumbing: a child whose tool calls
    are refused by the permission pack is still stopped (the retries are pointless), but
    the verdict it produces is PERMISSION_LOOP -> HARNESS_DEFECT, not KILLED ->
    INFRA_CRASH. This is the exact shape 1093/1090/511/1014 died in on 2026-08-29."""
    tracker, killed_reason, exit_code = run(tmp_path, looping=True,
                                            err="This command requires approval")
    assert killed_reason == "loop_break"
    assert tracker.trip_kind == "PERMISSION"

    raw = classifier.KILLED  # what the SIGTERM'd exit 143 classifies as on its own
    final = fworker.loop_break_classification(raw, killed_reason, tracker)
    assert final == classifier.PERMISSION_LOOP
    assert fworker.classify_block_reason(final) == fworker.BLOCK_HARNESS_DEFECT
    # the handoff a reviewer reads has to say whose bug it is
    assert "HARNESS DEFECT" in tracker.report()


def test_ordinary_loop_still_classifies_as_killed(tmp_path):
    """The override must be narrow: a real model loop keeps the KILLED/INFRA_CRASH
    verdict, so self-heal still requeues it and the breaker still counts it."""
    tracker, killed_reason, _ = run(tmp_path, looping=True)
    assert killed_reason == "loop_break" and tracker.trip_kind is None
    final = fworker.loop_break_classification(classifier.KILLED, killed_reason, tracker)
    assert final == classifier.KILLED
    assert fworker.classify_block_reason(final) == fworker.BLOCK_INFRA_CRASH


def test_healthy_child_untouched(tmp_path):
    tracker, killed_reason, exit_code = run(tmp_path, looping=False)
    assert killed_reason in (None, "post_result_stuck")
    assert tracker.tripped is None
