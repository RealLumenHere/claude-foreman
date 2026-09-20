"""wq#2138: two harness-side attempt losses.

1. action_gate RED-blocked prose inside a quoted heredoc as if it were a command.
2. The CLI's brace-with-quote refusal loop-broke a session that only needed to be
   told to use Write/Edit; the first such refusal is now a steer, not a strike."""
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import action_gate
import fworker

RM_PROSE = "Never run rm -rf / on a host; also drop table users is forbidden."


def verdict(cmd):
    return action_gate.classify_bash(cmd, {})[0]


def test_quoted_python_heredoc_prose_is_not_red():
    cmd = f"python3 - <<'EOF'\nopen('prompt.md','w').write('{RM_PROSE}')\nEOF"
    assert verdict(cmd) == "GREEN"


def test_quoted_cat_heredoc_prose_is_not_red():
    assert verdict(f"cat > notes.md <<'EOF'\n{RM_PROSE}\nEOF") == "GREEN"


def test_same_text_outside_heredoc_is_still_red():
    assert verdict("rm -rf /") == "RED"
    assert verdict("sqlite3 x.db 'drop table users'") == "RED"
    assert verdict(f"echo start\n{RM_PROSE.split(';')[0]}") == "RED"


def test_command_after_heredoc_terminator_is_still_scanned():
    assert verdict("cat > n.md <<'EOF'\nprose\nEOF\nrm -rf /") == "RED"


def test_redirect_target_on_heredoc_line_is_still_scanned():
    assert verdict("cat > /dev/sda <<'EOF'\nprose\nEOF") == "RED"


def test_unquoted_heredoc_body_is_still_scanned():
    assert verdict("cat > n.md <<EOF\nrm -rf /\nEOF") == "RED"


def test_heredoc_fed_to_a_shell_or_sql_client_is_still_scanned():
    assert verdict("bash <<'EOF'\nrm -rf /\nEOF") == "RED"
    assert verdict("cat <<'EOF' | sh\nrm -rf /\nEOF") == "RED"
    assert verdict("sqlite3 x.db <<'EOF'\ndrop table t;\nEOF") == "RED"
    assert verdict("x=$(cat <<'EOF'\nrm -rf /\nEOF\n)\nsh -c \"$x\"") == "RED"


def test_unterminated_heredoc_is_not_masked():
    assert verdict("cat <<'EOF'\nrm -rf /") == "RED"


def test_here_string_and_quoted_marker_do_not_open_a_heredoc():
    assert verdict("cat <<<'x'\nrm -rf /\nx") == "RED"
    assert verdict("echo \"<<'EOF'\"\nrm -rf /\nEOF") == "RED"


def test_rules_file_tamper_inside_a_heredoc_stays_red():
    cmd = "python3 - <<'EOF'\nimport os; os.system('rm .fworker-rules.yaml')\nEOF"
    assert verdict(cmd) == "RED"


REFUSAL = ("Command contains brace with quote character (expansion obfuscation) "
           "- requires approval")


def _frames(n, cmd="python3 - <<'EOF'"):
    for i in range(n):
        yield {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": cmd}}]}}
        yield {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True,
             "content": [{"type": "text", "text": REFUSAL}]}]}}


def test_first_refusal_steers_without_a_strike():
    t = action_gate.LoopTracker(threshold=3)
    assert [t.observe(f) for f in _frames(1)] == [None, None]
    assert t.steer == action_gate.SHELL_REFUSAL_STEER
    assert "Write" in t.steer and "Edit" in t.steer
    assert t.tripped is None and not t.fail_counts.get("bash:python3", 0)


def test_refusals_after_the_steer_loop_break_as_harness_defect():
    t = action_gate.LoopTracker(threshold=3, steered=True)
    out = [t.observe(f) for f in _frames(3)]
    assert out[-1] and t.tripped
    assert t.trip_kind == "PERMISSION" and t.steer is None
    assert fworker.loop_break_classification("KILLED", "loop_break", t) == "PERMISSION_LOOP"


def test_steer_then_three_refusals_is_steer_before_loop_break():
    t = action_gate.LoopTracker(threshold=3)
    seq = [t.observe(f) for f in _frames(1)]
    assert t.steer and not any(seq)
    resumed = action_gate.LoopTracker(threshold=3, steered=True)
    assert any(resumed.observe(f) for f in _frames(3))
    assert resumed.steer is None


def _child(tmp_path, rounds):
    script = textwrap.dedent(f"""\
        import json, time
        def emit(f):
            print(json.dumps(f), flush=True)
        emit({{"type": "system", "subtype": "init", "session_id": "steer-int", "model": "fake"}})
        for i in range({rounds}):
            emit({{"type": "assistant", "message": {{"content": [
                {{"type": "tool_use", "id": f"t{{i}}", "name": "Bash",
                  "input": {{"command": "python3 - <<'EOF'"}}}}]}}}})
            emit({{"type": "user", "message": {{"content": [
                {{"type": "tool_result", "tool_use_id": f"t{{i}}", "is_error": True,
                  "content": [{{"type": "text", "text": {json.dumps(REFUSAL)}}}]}}]}}}})
            time.sleep(0.05)
        time.sleep(5)
    """)
    p = tmp_path / "refuse_child.py"
    p.write_text(script)
    return [sys.executable, str(p)]


def _run(tmp_path, tracker):
    return fworker.run_child(
        _child(tmp_path, 5), str(tmp_path), dict(os.environ), max_turns=50,
        wall_clock_s=30, ticket_id=999999, attempt=1, frame_gap_s=10,
        post_result_grace_s=2, loop_tracker=tracker)


def test_run_child_stops_for_a_steer_then_loop_breaks_once_steered(tmp_path):
    tracker = action_gate.LoopTracker(threshold=3)
    state, _, _, killed_reason, _, _ = _run(tmp_path, tracker)
    assert killed_reason == "steer" and tracker.steer and state.session_id == "steer-int"
    tracker = action_gate.LoopTracker(threshold=3, steered=True)
    _, _, _, killed_reason, _, _ = _run(tmp_path, tracker)
    assert killed_reason == "loop_break" and tracker.trip_kind == "PERMISSION"


def test_worker_prompt_tells_workers_to_use_write_and_edit():
    text = open(os.path.join(os.path.dirname(__file__), "..", "prompts", "worker.md")).read()
    assert "Write and Edit tools, never with shell heredocs" in text
