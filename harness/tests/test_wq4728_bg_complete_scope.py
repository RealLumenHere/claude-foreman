"""wq#4728: a background-task COMPLETION must come from the CLI's notification channel,
not from any text that happens to say "task completed".

wq#4707 shipped BackgroundTaskWatch with an unscoped completion regex: it matched any
content block in any user frame, tool_result or not, with no link to a tracked
tool_use_id — unlike the start side, which gates on `tool_use_id not in self._bg_ids`.
wq#4726 showed the consequence: an ordinary foreground tool result whose text merely
contains "background task … completed" (reading a file, a test log, a ticket — the
fleet's own read-before-you-act doctrine) cleared a real pending task, dropping the
session back to the SHORT post-result grace with work still outstanding. That is
wq#4707's own failure mode — a live worker killed post_result_stuck mid-task — reached
through a trigger none of its 11 tests probe (wq#2630: every completion-side test fed the
structured wrapper, so the probe population could not falsify this axis).

The probes here are deliberately drawn from OUTSIDE that population: the committed
wq4686 replay fixture's own bytes as incidental tool output, a completion arriving before
any start, and the structured system/task_notification frame real Claude Code emits
(anthropics/claude-code#104943).
"""
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402

# Committed, immutable probe input: the wq4707 replay fixture contains a real
# "<task-notification>Background task bash_1 completed: 312 passed</task-notification>"
# line, so an agent that Reads/greps it produces exactly the incidental text at issue.
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "wq4707_wq4686_a2_frames.jsonl")

BG_START = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "bg1", "name": "Bash",
     "input": {"command": "sleep 99999", "run_in_background": True}}]}}


def _watch(frames):
    w = fworker.BackgroundTaskWatch()
    for f in frames:
        w.observe(f)
    return w


def _tool_result(tool_use_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": False,
         "content": [{"type": "text", "text": text}]}]}}


def _inline_child(tmp_path, name, body):
    p = tmp_path / f"{name}.py"
    p.write_text("import json, sys, time\n"
                 "def emit(f): print(json.dumps(f), flush=True)\n"
                 + textwrap.dedent(body))
    return [sys.executable, str(p)]


def _run(tmp_path, cmd, grace=1.0, bg_grace=5.0, wall=40):
    return fworker.run_child(
        cmd, str(tmp_path), dict(os.environ),
        max_turns=200, wall_clock_s=wall, ticket_id=999948, attempt=1,
        frame_gap_s=20, post_result_grace_s=grace, loop_tracker=None,
        post_result_bg_grace_s=bg_grace)


# --------------------------------------------------------------------------
# unit — wq#4726 repro 1
# --------------------------------------------------------------------------

def test_unrelated_tool_result_does_not_clear_a_pending_task():
    w = _watch([
        BG_START,
        _tool_result("read_tool_id",
                     "Background task bash_1 completed\nsome other line\n"),
    ])
    assert (w.started, w.pending) == (1, 1)


def test_reading_the_wq4707_fixture_does_not_clear_a_pending_task():
    """Probe input from outside the designed-for population, pinned to a committed
    file: the fixture's own bytes, delivered as an ordinary Read result."""
    text = open(FIXTURE).read()
    # the probe is live — this text really does trip the text-channel matchers
    assert fworker._BG_NOTIFY_RE.search(text)
    assert fworker._BG_COMPLETE_RE.search(text)
    w = _watch([BG_START, _tool_result("read_tool_id", text)])
    assert w.completed == 0, "incidental file content counted as a completion"
    # the same read also trips the START side (the fixture says "running in background
    # with ID"), which inflates `started` — deliberately safe, since an over-count only
    # stretches the grace for a process that is still alive.
    assert w.pending >= 1


def test_incidental_text_cannot_bank_a_completion_before_any_start():
    """pending is started - completed, so a match with nothing outstanding would hand a
    later real start a free completion. A worker's own dispatch prompt quotes this
    phrasing verbatim (this ticket's does)."""
    w = _watch([
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "ticket: the <task-notification> wrapper says"
                                     " 'background task ... completed'"}]}},
        BG_START,
    ])
    assert (w.completed, w.pending) == (0, 1)


# --------------------------------------------------------------------------
# unit — the channels that DO count
# --------------------------------------------------------------------------

def test_structured_system_task_notification_clears():
    """Real Claude Code emits system/subtype=task_notification with a terminal status as
    the authoritative completion signal (anthropics/claude-code#104943)."""
    w = _watch([BG_START, {"type": "system", "subtype": "task_notification",
                           "task_id": "bash_1", "status": "completed"}])
    assert w.pending == 0


def test_structured_notification_terminal_statuses_all_release():
    for status in ("failed", "stopped", "timed_out"):
        w = _watch([BG_START, {"type": "system", "subtype": "task_notification",
                               "task_id": "bash_1", "status": status}])
        assert w.pending == 0, status


def test_unrelated_system_frame_does_not_clear():
    w = _watch([BG_START, {"type": "system", "subtype": "init",
                           "session_id": "s", "model": "fake"}])
    assert w.pending == 1


def test_injected_notification_turn_still_clears():
    """wq4686's captured shape: the CLI hands the completion back as a plain text turn."""
    w = _watch([BG_START, {"type": "user", "message": {"content": [
        {"type": "text",
         "text": "<task-notification>Background task bash_1 completed</task-notification>"}
    ]}}])
    assert w.pending == 0


def test_tracked_tasks_own_result_still_clears():
    """A result for the call we counted as the start may speak for it — same id-scoping
    the start side uses, just inverted."""
    w = _watch([BG_START, _tool_result("bg1", "background task bash_1 exited (rc=0)")])
    assert w.pending == 0


# --------------------------------------------------------------------------
# integration — wq#4726 repro 2, through run_child
# --------------------------------------------------------------------------

def test_incidental_tool_result_does_not_revert_to_the_short_grace(tmp_path):
    """A real never-reporting background task, one piece of ordinary foreground work
    whose result incidentally matches, then the terminal result and silence. Before the
    fix: killed at the short grace (~1s) with bg_tasks_pending == 0 — a live worker shot
    with real work outstanding. After: held for the long grace, pending still 1."""
    cmd = _inline_child(tmp_path, "incidental", """
        emit({"type": "system", "subtype": "init", "session_id": "ix", "model": "fake"})
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "bg1", "name": "Bash",
             "input": {"command": "sleep 99999", "run_in_background": True}}]}})
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r1", "name": "Read",
             "input": {"file_path": "/home/lumen/kb/ISSUE-LOG.md"}}]}})
        emit({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1", "is_error": False,
             "content": [{"type": "text", "text":
                          "| 1607 | the background task bash_1 completed and the"
                          " worker was killed anyway | ... | SOLVED |"}]}]}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "waiting on the suite"})
        time.sleep(60)
    """)
    import time as _t
    t0 = _t.time()
    state, _exit, _events, killed_reason, _c, _p = _run(
        tmp_path, cmd, grace=1.0, bg_grace=5.0, wall=40)
    elapsed = _t.time() - t0
    assert killed_reason == "post_result_stuck"
    assert state.bg_tasks_pending == 1, "incidental text cleared a real pending task"
    assert 4.0 < elapsed < 15, f"short grace applied with a task pending ({elapsed:.1f}s)"


def test_structured_notification_releases_the_long_grace(tmp_path):
    """The other direction: once the CLI's structured notification lands, the session is
    an ordinary §C9 stuck process again and must not hold the long grace."""
    cmd = _inline_child(tmp_path, "structured", """
        emit({"type": "system", "subtype": "init", "session_id": "sx", "model": "fake"})
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "bg1", "name": "Bash",
             "input": {"command": "pytest", "run_in_background": True}}]}})
        emit({"type": "system", "subtype": "task_notification", "task_id": "bash_1",
              "status": "completed", "summary": "312 passed"})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
        time.sleep(60)
    """)
    import time as _t
    t0 = _t.time()
    state, _exit, _events, killed_reason, _c, _p = _run(
        tmp_path, cmd, grace=1.0, bg_grace=20.0, wall=40)
    elapsed = _t.time() - t0
    assert killed_reason == "post_result_stuck"
    assert state.bg_tasks_pending == 0
    assert elapsed < 8, f"long grace held after the task reported ({elapsed:.1f}s)"


def test_fixture_still_carries_the_incidental_phrasing():
    """Guards the probe itself: if the fixture stops containing notification text, the
    tool_result probes above would pass for the wrong reason."""
    raw = open(FIXTURE).read()
    assert "<task-notification>" in raw
    assert [f for f in (json.loads(ln) for ln in raw.splitlines() if ln.strip())
            if f.get("type") == "assistant"]
