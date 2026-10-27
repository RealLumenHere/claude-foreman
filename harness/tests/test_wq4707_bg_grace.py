"""wq#4707: the post-result grace must not kill a session that is waiting for a
background task it started, and must rearm when the session resumes work.

Measured cause (over /home/lumen/foreman/_runs/wq*/episode.jsonl attempt_end records):
73 attempts killed post_result_stuck, 60 scored FALSE_COMPLETE, 43 with an assistant
tail that says it is waiting on a background task. wq4686 attempt 1 ends on "Waiting
for the dispatcher test suite (background task bom0i2gan) to finish"; its attempt 2
dies 27s in after 4 live turns. Both had real work on disk (PR #2465 open).

Two defects, one test file:
  A. POST_RESULT_GRACE_S (20s) applied even with a background task outstanding, so the
     20s window expired long before any test suite could report back.
  B. result_seen_at was only ever set, never cleared, so the countdown opened by the
     FIRST result frame killed the session 20s later no matter how much work followed
     it — the ~25s, 4-7-turn attempt 2 signature.

Real subprocesses emitting stream-json, same plumbing as a live `claude -p`; windows
shrunk so the file runs in ~10s. The wq4686 replay is driven by a committed fixture
(fixtures/wq4707_wq4686_a2_frames.jsonl) so the probe input cannot drift.
"""
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "wq4707_wq4686_a2_frames.jsonl")


def _replay_child(tmp_path, frames_path):
    """Emit a fixture's frames in order, honouring each frame's `_pause_s`."""
    script = textwrap.dedent("""\
        import json, sys, time
        for line in open(sys.argv[1]):
            line = line.strip()
            if not line:
                continue
            frame = json.loads(line)
            pause = frame.pop("_pause_s", 0)
            if pause:
                time.sleep(pause)
            print(json.dumps(frame), flush=True)
    """)
    p = tmp_path / "replay_child.py"
    p.write_text(script)
    return [sys.executable, str(p), frames_path]


def _inline_child(tmp_path, name, body):
    p = tmp_path / f"{name}.py"
    p.write_text("import json, sys, time\n"
                 "def emit(f): print(json.dumps(f), flush=True)\n"
                 + textwrap.dedent(body))
    return [sys.executable, str(p)]


def _run(tmp_path, cmd, grace=1.0, bg_grace=8.0, wall=30):
    return fworker.run_child(
        cmd, str(tmp_path), dict(os.environ),
        max_turns=200, wall_clock_s=wall, ticket_id=999947, attempt=1,
        frame_gap_s=10, post_result_grace_s=grace, loop_tracker=None,
        post_result_bg_grace_s=bg_grace)


def _result_texts(events):
    return [e.get("result") for e in events if e.get("type") == "result"]


# --------------------------------------------------------------------------
# BackgroundTaskWatch unit probes — including shapes the Bash(run_in_background)
# case this fix was designed around does not cover (wq#2630: a probe drawn only
# from the designed-for population can only confirm).
# --------------------------------------------------------------------------

def _watch(frames):
    w = fworker.BackgroundTaskWatch()
    for f in frames:
        w.observe(f)
    return w


def test_watch_counts_background_bash():
    w = _watch([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash",
             "input": {"command": "pytest", "run_in_background": True}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": False,
             "content": [{"type": "text",
                          "text": "Command running in background with ID: bash_1"}]}]}},
    ])
    # the start's own "running in background" result must not count a second task
    assert (w.started, w.pending) == (1, 1)


def test_watch_clears_on_completion_notification():
    w = _watch([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash",
             "input": {"command": "pytest", "run_in_background": True}}]}},
        {"type": "user", "message": {"content": [
            {"type": "text",
             "text": "<task-notification>Background task bash_1 completed</task-notification>"}]}},
    ])
    assert w.pending == 0


def test_watch_counts_agent_tool_without_the_flag():
    # OUTSIDE the designed-for population: the Agent tool backgrounds by default, so
    # no run_in_background key appears in the call at all.
    w = _watch([{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "a", "name": "Agent",
         "input": {"prompt": "review the diff"}}]}}])
    assert w.pending == 1


def test_watch_counts_cli_auto_backgrounded_command():
    # OUTSIDE too: wq4239's `lumen-pr land`, which the agent ran in the FOREGROUND and
    # the CLI moved to the background on its own tool timeout. Only the result says so.
    w = _watch([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash",
             "input": {"command": "/home/lumen/bin/lumen-pr land"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": False,
             "content": [{"type": "text", "text":
                          "Command timed out after 120s and was moved to the background"
                          " (task id t9); you will be notified when it exits."}]}]}},
    ])
    assert w.pending == 1


def test_watch_ignores_ordinary_foreground_work():
    w = _watch([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash",
             "input": {"command": "pytest -x", "run_in_background": False}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": False,
             "content": [{"type": "text", "text": "312 passed"}]}]}},
    ])
    assert w.pending == 0


# --------------------------------------------------------------------------
# run_child integration
# --------------------------------------------------------------------------

def test_wq4686_attempt2_replay_survives_to_the_land(tmp_path):
    """Defects A and B together, on the shape wq4686 attempt 2 actually had: an early
    result frame, four live turns, a background suite, then the notification. Before
    the fix the child was SIGTERM'd ~1 grace after the FIRST result and never reached
    the land step."""
    state, exit_code, events, killed_reason, _cpu, _pid = _run(
        tmp_path, _replay_child(tmp_path, FIXTURE))
    assert killed_reason is None, f"killed as {killed_reason} mid-ticket"
    assert exit_code == 0
    assert "landed" in _result_texts(events)
    assert state.bg_tasks_started == 1 and state.bg_tasks_pending == 0


def test_waiting_on_background_task_gets_the_long_grace(tmp_path):
    """Defect A alone: result frame, then silence while the task runs, then the
    notification. The short grace (1s) must not apply; the long one (8s) must."""
    cmd = _inline_child(tmp_path, "bg_wait", """
        emit({"type": "system", "subtype": "init", "session_id": "bg", "model": "fake"})
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "pytest", "run_in_background": True}}]}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "waiting"})
        time.sleep(3)
        emit({"type": "user", "message": {"content": [
            {"type": "text", "text": "<task-notification>task bash_1 completed"
                                     "</task-notification>"}]}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "done"})
    """)
    state, exit_code, events, killed_reason, _c, _p = _run(tmp_path, cmd)
    assert killed_reason is None
    assert "done" in _result_texts(events)


def test_stuck_after_result_is_still_killed_fast(tmp_path):
    """§C9 regression guard: no background task, result landed, process won't exit —
    the SHORT grace must still fire, and the long one must not be waited out."""
    cmd = _inline_child(tmp_path, "stuck", """
        emit({"type": "system", "subtype": "init", "session_id": "st", "model": "fake"})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
        time.sleep(30)
    """)
    import time as _t
    t0 = _t.time()
    state, exit_code, events, killed_reason, _c, _p = _run(tmp_path, cmd)
    assert killed_reason == "post_result_stuck"
    assert _t.time() - t0 < 6, "waited the background grace with no background task"


def test_completed_background_task_does_not_hold_the_session_open(tmp_path):
    """The long grace must be released once the task reports: a session that started a
    background task, got its notification, and THEN hangs after the final result is an
    ordinary §C9 stuck process on the short grace."""
    cmd = _inline_child(tmp_path, "bg_then_stuck", """
        emit({"type": "system", "subtype": "init", "session_id": "bs", "model": "fake"})
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "pytest", "run_in_background": True}}]}})
        emit({"type": "user", "message": {"content": [
            {"type": "text", "text": "<task-notification>task bash_1 completed"
                                     "</task-notification>"}]}})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
        time.sleep(30)
    """)
    import time as _t
    t0 = _t.time()
    state, exit_code, events, killed_reason, _c, _p = _run(tmp_path, cmd)
    assert killed_reason == "post_result_stuck"
    assert _t.time() - t0 < 6
    assert state.bg_tasks_started == 1 and state.bg_tasks_pending == 0


def test_never_reporting_background_task_is_bounded(tmp_path):
    """A task that never reports back must still end the session — at the long grace,
    not never. Keeps the fix from turning one false-complete into a hung lane."""
    cmd = _inline_child(tmp_path, "bg_never", """
        emit({"type": "system", "subtype": "init", "session_id": "bn", "model": "fake"})
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "sleep 99999", "run_in_background": True}}]}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "waiting"})
        time.sleep(60)
    """)
    import time as _t
    t0 = _t.time()
    state, exit_code, events, killed_reason, _c, _p = _run(
        tmp_path, cmd, grace=1.0, bg_grace=3.0, wall=40)
    elapsed = _t.time() - t0
    assert killed_reason == "post_result_stuck"
    assert 2.5 < elapsed < 10, f"long grace not honoured/bounded ({elapsed:.1f}s)"
    assert state.bg_tasks_pending == 1


def test_fixture_is_the_wq4686_shape():
    """The replay fixture stays what the episode record described: an early empty
    result, a backgrounded suite, a completion notification, a final result."""
    frames = [json.loads(ln) for ln in open(FIXTURE) if ln.strip()]
    results = [f for f in frames if f.get("type") == "result"]
    assert results[0].get("result") == ""
    assert any(b.get("input", {}).get("run_in_background")
               for f in frames if f.get("type") == "assistant"
               for b in f["message"]["content"] if b.get("type") == "tool_use")
    assert any("task-notification" in json.dumps(f) for f in frames)
