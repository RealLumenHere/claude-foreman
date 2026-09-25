import json
import os
import shutil
import subprocess

import pytest

import mb_agentic as ag
from mb_node import Refused


def suite():
    return ag.load_suite()


def test_suite_is_four_edit_four_probe():
    kinds = [t["kind"] for t in suite()]
    assert len(kinds) == 8
    assert kinds.count("mechanical-edit") == 4 and kinds.count("probe-build") == 4


@pytest.mark.parametrize("task", suite(), ids=lambda t: t["id"])
def test_task_fails_on_baseline_and_passes_with_reference_solution(task, tmp_path):
    repo = ag.build_repo(task, str(tmp_path))
    ok, _ = ag.run_test(task, repo)
    assert not ok, "baseline must fail or the task measures nothing"
    sol = os.path.join(task["dir"], "solution")
    shutil.copytree(sol, repo, dirs_exist_ok=True)
    ok, tail = ag.run_test(task, repo)
    assert ok, tail
    files, lines = ag.diff_stats(repo, set(task.get("protected", [])))
    assert files > 0 and lines > 0


def _same_size_same_mtime_task(tmp_path):
    """A task whose fix is a one-character edit: source size and mtime stay put, so a
    stale `.pyc` compiled before the fix still validates against it."""
    task_dir = tmp_path / "task"
    (task_dir / "repo" / "pkg").mkdir(parents=True)
    (task_dir / "repo" / "tests").mkdir(parents=True)
    (task_dir / "repo" / "pkg" / "__init__.py").write_text("")
    (task_dir / "repo" / "pkg" / "config.py").write_text("V = 1\n")
    (task_dir / "repo" / "tests" / "test_v.py").write_text(
        "import unittest\nfrom pkg import config\n"
        "class T(unittest.TestCase):\n"
        "    def test_v(self):\n        self.assertEqual(config.V, 2)\n")
    return {"id": "stale", "dir": str(task_dir), "protected": ["tests/test_v.py"],
            "test_cmd": "python3 -m unittest discover -s tests -q"}


def _apply_fix_preserving_size_and_mtime(repo):
    src = os.path.join(repo, "pkg", "config.py")
    st = os.stat(src)
    with open(src, "w") as fh:
        fh.write("V = 2\n")
    os.utime(src, (st.st_atime, st.st_mtime))
    assert os.stat(src).st_size == st.st_size and os.stat(src).st_mtime == st.st_mtime


def test_scoring_run_sees_a_fix_that_kept_the_source_size_and_mtime(tmp_path):
    """The offline baseline-then-solution shape: two run_test() calls in one repo dir."""
    task = _same_size_same_mtime_task(tmp_path)
    repo = ag.build_repo(task, str(tmp_path / "root"))
    ok, _ = ag.run_test(task, repo)
    assert not ok
    _apply_fix_preserving_size_and_mtime(repo)
    ok, tail = ag.run_test(task, repo)
    assert ok, tail


def test_a_models_own_self_check_cannot_poison_the_score(tmp_path):
    """Task prompts tell the model to run the test itself mid-session; that write of
    `__pycache__` must not be what the final scoring run reads."""
    task = _same_size_same_mtime_task(tmp_path)
    repo = ag.build_repo(task, str(tmp_path / "root"))
    subprocess.run(task["test_cmd"], shell=True, cwd=repo, capture_output=True)
    assert os.path.isdir(os.path.join(repo, "pkg", "__pycache__")), "self-check left no cache"
    _apply_fix_preserving_size_and_mtime(repo)
    ok, tail = ag.run_test(task, repo)
    assert ok, tail


def test_diff_is_counted_when_an_edit_kept_the_source_size_and_mtime(tmp_path):
    task = _same_size_same_mtime_task(tmp_path)
    repo = ag.build_repo(task, str(tmp_path / "root"))
    _apply_fix_preserving_size_and_mtime(repo)
    assert ag.diff_stats(repo, set(task["protected"])) == (1, 2)


def test_scoring_leaves_no_bytecode_behind(tmp_path):
    task = suite()[0]
    repo = ag.build_repo(task, str(tmp_path))
    ag.run_test(task, repo)
    assert not any("__pycache__" in dirs for _, dirs, _ in os.walk(repo))


def test_editing_the_test_does_not_pass_a_task(tmp_path):
    task = next(t for t in suite() if t["id"] == "e1_rename")
    repo = ag.build_repo(task, str(tmp_path))
    with open(os.path.join(repo, "tests/test_rename.py"), "w") as fh:
        fh.write("import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n")
    ok, _ = ag.run_test(task, repo)                            # pristine test is restored first
    assert not ok


def test_parse_stream_counts_turns_calls_and_errors():
    frames = [
        {"type": "system", "subtype": "init", "model": "hathor-llama8b"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "2"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": False}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
         "duration_ms": 1234},
    ]
    s = ag.parse_stream("\n".join(json.dumps(f) for f in frames) + "\nnot json\n")
    assert (s["turns"], s["tool_calls"], s["tool_errors"]) == (3, 2, 1)
    assert s["model_reported"] == "hathor-llama8b" and s["result_subtype"] == "success"


def test_command_uses_the_fworker_local_profile():
    from harness import fworker
    cmd = ag.claude_cmd(suite()[0], "hathor-llama8b", "claude")
    for flag in fworker.LOCAL_CLI_FLAGS:
        assert flag in cmd
    assert cmd[cmd.index("--model") + 1] == "hathor-llama8b"
    assert cmd[cmd.index("--settings") + 1] == ag.SETTINGS


def test_env_is_gateway_and_local_profile_without_cloud_auth(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "leak")
    env = ag.gateway_env("m", "http://gw:4000", "tok")
    assert env["ANTHROPIC_BASE_URL"] == "http://gw:4000" and env["ANTHROPIC_AUTH_TOKEN"] == "tok"
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"


def test_run_task_scores_a_real_fix_through_a_fake_claude(tmp_path):
    """A stand-in `claude` that applies the reference solution and emits stream-json."""
    task = next(t for t in suite() if t["id"] == "p4_env")
    fake = tmp_path / "claude"
    fake.write_text(
        "#!/bin/sh\n"
        f"cp -r {task['dir']}/solution/. .\n"
        'echo \'{"type":"assistant","message":{"content":[{"type":"tool_use","id":"1"}]}}\'\n'
        'echo \'{"type":"result","subtype":"success","is_error":false,"num_turns":2}\'\n')
    fake.chmod(0o755)
    r = ag.run_task(task, "m", str(tmp_path / "root"), "http://gw", "tok", str(fake), 60)
    assert r["success"] and r["test_pass"] and r["turns"] == 2 and r["tool_calls"] == 1
    assert r["diff_files"] == 1 and not r["timed_out"]


def test_run_task_fails_when_nothing_changed(tmp_path):
    task = next(t for t in suite() if t["id"] == "p4_env")
    fake = tmp_path / "claude"
    fake.write_text('#!/bin/sh\necho \'{"type":"result","subtype":"success","num_turns":1}\'\n')
    fake.chmod(0o755)
    r = ag.run_task(task, "m", str(tmp_path / "root"), "http://gw", "tok", str(fake), 60)
    assert not r["success"] and not r["test_pass"]


def test_summary():
    recs = [{"success": True, "kind": "mechanical-edit", "turns": 4, "wall_s": 10.0,
             "tool_calls": 5, "tool_errors": 1},
            {"success": False, "kind": "probe-build", "turns": 8, "wall_s": 30.0,
             "tool_calls": 9, "tool_errors": 3}]
    s = ag.summarize(recs)
    assert s["agentic_score"] == 0.5 and s["success_by_kind"]["probe-build"] == "0/1"
    assert (s["tool_errors"], s["wall_total_s"], s["turns_median"]) == (4, 40.0, 6.0)


def test_render_lists_agentic_tasks_and_summary():
    from mb_render import render
    base = {"row": "baseline-x", "run": "r1", "node": "n", "model": "x"}
    recs = [{**base, "phase": "agentic_task", "task": "e1_rename", "kind": "mechanical-edit",
             "success": False, "turns": 3, "tool_calls": 2, "tool_errors": 1, "wall_s": 12.0,
             "diff_lines": 0},
            {**base, "phase": "agentic_summary", "successes": 0, "tasks": 1}]
    md = render(recs, "n")
    assert "| baseline-x |" in md and "0/1" in md
    assert "| e1_rename | mechanical-edit | no | 3 | 2 | 1 | 12 | 0 |" in md


def test_serving_group_alias_needs_flag():
    with pytest.raises(Refused):
        ag.guard_model("hathor-llama8b", False)
    ag.guard_model("hathor-llama8b", True)
    ag.guard_model("some-other-model", False)


def test_git_repo_is_fresh_each_time(tmp_path):
    task = suite()[0]
    repo = ag.build_repo(task, str(tmp_path))
    open(os.path.join(repo, "junk"), "w").close()
    repo = ag.build_repo(task, str(tmp_path))
    out = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    assert out.stdout == ""
