"""wq#4634: a transport ceiling (Bash tool timeout, supergateway group reap /
Worker 70s abort — reports/wq3264_mcpw_commander_drops_rca.md) kills a RUNNING
command. fworker detects the kill class in stream-json, re-runs the command
detached under /home/lumen/bin/watchdog, waits for result.json, and resumes the
session with the verdict. Design: design/wq4634_tool_ceiling_watchdog_reissue.md."""
import json
import os
import stat
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import action_gate
import fworker

BASH_TIMEOUT = "Error: Command timed out after 2m 0.0s"
MCP_ABORT = "MCP error -32001: Request timed out"
ORDINARY_FAIL = "ModuleNotFoundError: No module named 'requests'\nexit 1"


def _tool_use(i, name="Bash", tool_input=None):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"t{i}", "name": name,
         "input": tool_input if tool_input is not None
                  else {"command": "pytest -x tests/"}}]}}


def _tool_result(i, text, is_error=True):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": is_error,
         "content": [{"type": "text", "text": text}]}]}}


# ---- detection: is_ceiling_kill / LoopTracker.ceiling -------------------------------

def test_bash_timeout_is_a_ceiling_kill():
    assert action_gate.is_ceiling_kill(BASH_TIMEOUT, "Bash")


def test_signal_kill_is_a_ceiling_kill():
    assert action_gate.is_ceiling_kill("command killed by signal SIGKILL", "Bash")
    assert action_gate.is_ceiling_kill("SIGTERM received, shutting down", "Bash")


def test_ordinary_failure_is_not_a_ceiling_kill():
    # test-to-failure: the detector must be able to say no.
    assert not action_gate.is_ceiling_kill(ORDINARY_FAIL, "Bash")
    assert not action_gate.is_ceiling_kill("exit 1 after 3s: tests failed", "Bash")
    assert not action_gate.is_ceiling_kill("", "Bash")


def test_mcp_transport_patterns_are_gated_on_the_tool_name():
    # "context canceled" in ordinary Bash OUTPUT is the command's own business
    # (a failing Go test prints it); only an MCP tool's error is a transport kill.
    assert not action_gate.is_ceiling_kill("FAIL: context canceled", "Bash")
    assert action_gate.is_ceiling_kill(
        "context canceled", "mcp__desktop-commander__start_process")
    assert action_gate.is_ceiling_kill(
        MCP_ABORT, "mcp__desktop-commander__start_process")


def test_tracker_captures_the_killed_command_without_a_strike():
    t = action_gate.LoopTracker(threshold=3)
    assert t.observe(_tool_use(0)) is None
    assert t.observe(_tool_result(0, BASH_TIMEOUT)) is None
    assert t.ceiling == {"command": "pytest -x tests/", "error": BASH_TIMEOUT}
    assert t.ceiling_spent and t.tripped is None
    assert sum(t.fail_counts.values()) == 0


def test_tracker_ignores_ceiling_text_on_a_successful_result():
    t = action_gate.LoopTracker(threshold=3)
    t.observe(_tool_use(0))
    t.observe(_tool_result(0, BASH_TIMEOUT, is_error=False))
    assert t.ceiling is None


def test_tracker_ignores_tools_without_a_command():
    t = action_gate.LoopTracker(threshold=3)
    t.observe(_tool_use(0, name="Read", tool_input={"file_path": "/tmp/x"}))
    t.observe(_tool_result(0, BASH_TIMEOUT))
    assert t.ceiling is None


def test_spent_tracker_counts_a_second_kill_as_an_ordinary_error():
    t = action_gate.LoopTracker(threshold=3, ceiling_spent=True)
    t.observe(_tool_use(0))
    t.observe(_tool_result(0, BASH_TIMEOUT))
    assert t.ceiling is None
    assert sum(t.fail_counts.values()) == 1


def test_mcp_start_process_command_is_captured():
    t = action_gate.LoopTracker(threshold=3)
    t.observe(_tool_use(0, name="mcp__desktop-commander__start_process",
                        tool_input={"command": "make iso", "timeout_ms": 60000}))
    t.observe(_tool_result(0, MCP_ABORT))
    assert t.ceiling["command"] == "make iso"


# ---- run_child: ceiling kill stops the session like a steer -------------------------

def _child(tmp_path, result_text):
    script = textwrap.dedent("""\
        import json, sys, time
        def emit(f):
            print(json.dumps(f), flush=True)
        emit({"type": "system", "subtype": "init", "session_id": "ceil-int",
              "model": "fake"})
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t0", "name": "Bash",
             "input": {"command": "sleep 300"}}]}})
        emit({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t0", "is_error": True,
             "content": [{"type": "text", "text": sys.argv[1]}]}]}})
        time.sleep(5)
    """)
    p = tmp_path / "ceiling_child.py"
    p.write_text(script)
    return [sys.executable, str(p), result_text]


def _run(tmp_path, tracker, result_text):
    return fworker.run_child(
        _child(tmp_path, result_text), str(tmp_path), dict(os.environ),
        max_turns=50, wall_clock_s=30, ticket_id=999634, attempt=1,
        frame_gap_s=10, post_result_grace_s=2, loop_tracker=tracker)


def test_run_child_stops_with_tool_ceiling(tmp_path):
    tracker = action_gate.LoopTracker(threshold=3)
    state, _, _, killed_reason, _, _ = _run(tmp_path, tracker, BASH_TIMEOUT)
    assert killed_reason == "tool_ceiling"
    assert state.session_id == "ceil-int"
    assert tracker.ceiling["command"] == "sleep 300"


def test_run_child_does_not_stop_for_an_ordinary_error(tmp_path):
    # test-to-failure: the same frames with a non-ceiling error run to EOF.
    tracker = action_gate.LoopTracker(threshold=3)
    _, _, _, killed_reason, _, _ = _run(tmp_path, tracker, ORDINARY_FAIL)
    assert killed_reason != "tool_ceiling" and tracker.ceiling is None


# ---- reissue_ceiling_killed ----------------------------------------------------------

def _fake_watchdog(tmp_path, body):
    p = tmp_path / "fake_watchdog"
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _cleanup(label):
    for suffix in ("result.json", "out", "tail", "log", "rc", "pid"):
        try:
            os.remove(f"/tmp/watchdog-{label}.{suffix}")
        except OSError:
            pass


def test_reissue_waits_for_result_and_returns_the_verdict(tmp_path, monkeypatch):
    label = "fw-999634-a1"
    _cleanup(label)
    # fake watchdog: daemonise-style — write result.json, exit 0 immediately.
    result = {"status": "OK", "rc": 0, "note": "exit 0 after 312s",
              "tail": f"/tmp/watchdog-{label}.tail", "out": f"/tmp/watchdog-{label}.out",
              "duration_s": 312}
    fake = _fake_watchdog(tmp_path, textwrap.dedent(f"""\
        echo "$@" > {tmp_path}/argv
        cat > /tmp/watchdog-{label}.result.json <<'JSON'
        {json.dumps(result)}
        JSON
        exit 0
    """))
    monkeypatch.setattr(fworker, "WATCHDOG_BIN", fake)
    line = fworker.reissue_ceiling_killed(
        999634, 1, {"command": "sleep 300", "error": BASH_TIMEOUT},
        str(tmp_path), timeout_min=1, poll_s=0.1, grace_s=5)
    assert line.startswith(f"[watchdog:{label}] OK — exit 0 after 312s")
    assert f"/tmp/watchdog-{label}.tail" in line
    assert "Do NOT re-run the command bare" in line
    argv = (tmp_path / "argv").read_text()
    assert "--no-send" in argv and "sleep 300" in argv and f"--label {label}" in argv
    _cleanup(label)


def test_reissue_refuses_a_red_command(tmp_path, monkeypatch):
    # fworker re-runs outside the PreToolUse hook, so RED must fail closed.
    fake = _fake_watchdog(tmp_path, f"touch {tmp_path}/ran\nexit 0\n")
    monkeypatch.setattr(fworker, "WATCHDOG_BIN", fake)
    line = fworker.reissue_ceiling_killed(
        999634, 2, {"command": "rm -rf /", "error": BASH_TIMEOUT},
        str(tmp_path), timeout_min=1, poll_s=0.1, grace_s=1)
    assert "REFUSED" in line and "RED" in line
    assert not (tmp_path / "ran").exists()


def test_reissue_reports_a_spawn_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "WATCHDOG_BIN", str(tmp_path / "missing-bin"))
    line = fworker.reissue_ceiling_killed(
        999634, 3, {"command": "sleep 300", "error": BASH_TIMEOUT},
        str(tmp_path), timeout_min=1, poll_s=0.1, grace_s=1)
    assert "SPAWN-FAILED" in line and "NOT re-run" in line


def test_reissue_reports_no_verdict_when_result_never_lands(tmp_path, monkeypatch):
    label = "fw-999634-a4"
    _cleanup(label)
    fake = _fake_watchdog(tmp_path, "exit 0\n")   # daemonised but never verdicts
    monkeypatch.setattr(fworker, "WATCHDOG_BIN", fake)
    line = fworker.reissue_ceiling_killed(
        999634, 4, {"command": "sleep 300", "error": BASH_TIMEOUT},
        str(tmp_path), timeout_min=0, poll_s=0.1, grace_s=1)
    assert "NO-VERDICT" in line and f"/tmp/watchdog-{label}.result.json" in line


def test_reissue_handles_a_lost_command():
    line = fworker.reissue_ceiling_killed(999634, 5, {}, "/tmp",
                                          timeout_min=0, poll_s=0.1, grace_s=1)
    assert "SKIPPED" in line


# ---- end-to-end against the real watchdog script -------------------------------------

def test_real_watchdog_roundtrip(tmp_path):
    """The contract test: the REAL /home/lumen/bin/watchdog re-runs a short command
    and reissue_ceiling_killed returns its OK verdict. Skipped where the script is
    not installed (CI off-box)."""
    import pytest
    if not os.access(fworker.WATCHDOG_BIN, os.X_OK):
        pytest.skip(f"{fworker.WATCHDOG_BIN} not installed")
    label = "fw-999634-a6"
    _cleanup(label)
    marker = tmp_path / "ran-under-watchdog"
    line = fworker.reissue_ceiling_killed(
        999634, 6, {"command": f"touch {marker}", "error": BASH_TIMEOUT},
        str(tmp_path), timeout_min=1, poll_s=0.5, grace_s=30)
    assert line.startswith(f"[watchdog:{label}] OK"), line
    assert marker.exists()
    _cleanup(label)
