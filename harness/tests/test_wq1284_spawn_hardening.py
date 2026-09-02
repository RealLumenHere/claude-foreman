"""wq#1284 / ISSUE-SOLUTIONS-fable.md #18 (in-harness anchor): run_child()'s
subprocess.Popen must pass start_new_session=True so the `claude -p` child lands in its
own session/process group. Without it the child shares fworker's process group, so a
signal delivered to that group (an interactive connector recycling the whole group, per
ISSUE-LOG #18: "dispatches never execute") kills the child too — the exact failure mode
dispatch-batch (lumen-bin, wq#1255) fixes one level up for the dispatch call itself.
"""
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402


def make_child(tmp_path):
    """A minimal stream-json child: init, then a result frame."""
    script = textwrap.dedent("""\
        import json
        def emit(f):
            print(json.dumps(f), flush=True)
        emit({"type": "system", "subtype": "init", "session_id": "spawn-hardening",
              "model": "fake"})
        emit({"type": "result", "subtype": "success", "is_error": False})
    """)
    p = tmp_path / "ok_child.py"
    p.write_text(script)
    return [sys.executable, str(p)]


def test_run_child_passes_start_new_session(monkeypatch, tmp_path):
    """Directly asserts the Popen kwargs run_child() uses: start_new_session=True is the
    structural guarantee that survives a connector recycling fworker's own process
    group; stdin stays DEVNULL and stdout stays piped (captured into the per-ticket
    events log by the caller) unchanged."""
    captured = {}
    real_popen = fworker.subprocess.Popen

    class _Recording:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self._proc = real_popen(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._proc, name)

    monkeypatch.setattr(fworker.subprocess, "Popen", _Recording)
    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child(
        make_child(tmp_path), str(tmp_path), dict(os.environ),
        max_turns=50, wall_clock_s=10, ticket_id=999997, attempt=1,
        frame_gap_s=5, post_result_grace_s=1)

    assert captured.get("start_new_session") is True
    assert captured.get("stdin") == fworker.subprocess.DEVNULL
    assert captured.get("stdout") == fworker.subprocess.PIPE
    assert exit_code == 0
    assert killed_reason is None


def test_child_lands_in_its_own_process_group(tmp_path):
    """End-to-end over the real plumbing (no mocking): the child's pgid must differ from
    the test process's pgid, which is only true when start_new_session put it in a new
    session. Sampled via a child that reports its own pgid before exiting, since
    run_child() only returns after the child has already exited and been reaped."""
    script = textwrap.dedent("""\
        import json, os
        print(json.dumps({"type": "system", "subtype": "init",
                           "session_id": "pgid-check", "model": "fake"}), flush=True)
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                           "pgid": os.getpgrp()}), flush=True)
    """)
    p = tmp_path / "pgid_child.py"
    p.write_text(script)

    parent_pgid = os.getpgrp()
    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child(
        [sys.executable, str(p)], str(tmp_path), dict(os.environ),
        max_turns=50, wall_clock_s=10, ticket_id=999996, attempt=1,
        frame_gap_s=5, post_result_grace_s=1)

    result_frames = [e for e in events if e.get("type") == "result"]
    assert result_frames, f"no result frame in {events}"
    child_pgid = result_frames[-1]["pgid"]
    assert child_pgid != parent_pgid
    assert child_pgid == pid  # start_new_session=True makes the child its own group leader
