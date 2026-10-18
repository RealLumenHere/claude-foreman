"""wq#4289: fworker's aider runtime (run_child_aider and its landing helpers).

A fake aider (a real subprocess, plain-text output like the real one) runs in a real
linked git worktree; lumen-pr and wq are stubbed at fworker's own seams (_lumen_pr,
wq_done, wq_show) so the test proves what the harness does with aider's result without
pushing anything. The verdict is checked through classify(), the same call do_attempt()
makes, so "aider path SUCCESS" means what it means for a claude child.
"""
import os
import re
import stat
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402
from classifier import classify, SUCCESS, CRASH  # noqa: E402

# bin/lumen-pr's SUBJECT_RE, copied so a drift there shows up as a failing test here.
SUBJECT_RE = (r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
              r"(\([a-z0-9._-]+\))?!?: .{1,60}$")

FAKE_AIDER = textwrap.dedent("""\
    #!/usr/bin/env python3
    import os, sys
    mode = os.environ.get("FAKE_AIDER_MODE", "edit")
    print("Aider v0.0 fake", flush=True)
    print("argv-has-no-auto-commits=" + str("--no-auto-commits" in sys.argv), flush=True)
    print("backend-base=" + os.environ.get("OPENAI_API_BASE", "<unset>"), flush=True)
    os.makedirs(".aider.tags.cache.v4", exist_ok=True)
    open(".aider.tags.cache.v4/cache.db", "w").write("x")
    if mode == "edit":
        with open("docs/note.md", "a") as f:
            f.write("added by aider\\n")
        open("docs/new.md", "w").write("new\\n")
    elif mode == "api_error":
        print("litellm.APIConnectionError: connection refused", flush=True)
    sys.exit(3 if mode == "crash" else 0)
""")


@pytest.fixture()
def worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    g = ["git", "-C", str(repo)]
    subprocess.run(g + ["init", "-q", "-b", "main"], check=True)
    subprocess.run(g + ["config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(g + ["config", "user.name", "t"], check=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("note\n")
    subprocess.run(g + ["add", "docs/note.md"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(g + ["worktree", "add", "-q", str(wt), "-b", "docs/wq1-x-a1"], check=True)
    return wt


@pytest.fixture()
def harness(tmp_path, monkeypatch):
    """fake aider + env file + run dir; records every lumen-pr / wq done call."""
    aider = tmp_path / "fake-aider"
    aider.write_text(FAKE_AIDER)
    aider.chmod(aider.stat().st_mode | stat.S_IXUSR)
    envf = tmp_path / "aider.env"
    envf.write_text("export OPENAI_API_BASE=http://127.0.0.1:9/v1\n"
                    "OPENAI_API_KEY='" + "k" * 6 + "'\n")
    rd = tmp_path / "run"
    rd.mkdir()
    calls = {"lumen_pr": [], "done": []}

    def fake_lumen_pr(env, *args):
        calls["lumen_pr"].append(args)
        if args[0] == "commit":
            subprocess.run(["git", "-C", args[1], "commit", "-q", "-m", args[2]], check=True)
        if args[0] == "open":
            return "https://github.com/RealLumenHere/x/pull/42\n"
        return ""

    monkeypatch.setattr(fworker, "AIDER_BIN", str(aider))
    monkeypatch.setattr(fworker, "AIDER_ENV_FILE", str(envf))
    monkeypatch.setattr(fworker, "run_dir", lambda tid: str(rd))
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {
        "status": "claimed", "title": "FOLLOW-UP wq#7: Document the thing.",
        "detail": "Add a line to docs/note.md."})
    monkeypatch.setattr(fworker, "_lumen_pr", fake_lumen_pr)
    monkeypatch.setattr(fworker, "wq_done", lambda tid, notes, **kw: calls["done"].append(notes))
    return calls


def _run(worktree, monkeypatch, mode):
    monkeypatch.setenv("FAKE_AIDER_MODE", mode)
    return fworker.run_child_aider(
        ["claude", "-p", "ignored"], str(worktree), dict(os.environ), 40, 60, 4289, 1,
        frame_gap_s=20, post_result_grace_s=5)


def test_edit_lands_and_classifies_success(worktree, harness, monkeypatch):
    state, exit_code, events, killed, _, _ = _run(worktree, monkeypatch, "edit")
    assert killed is None and exit_code == 0
    assert classify(state, exit_code) == SUCCESS
    assert fworker.parse_worker_sentinel(state.result_text) == "TICKET_CLOSED"
    verbs = [c[0] for c in harness["lumen_pr"]]
    assert verbs == ["commit", "open", "land"]
    assert harness["lumen_pr"][2] == ("land", str(worktree), "42")
    assert harness["lumen_pr"][0][3:] == ("--refs", "wq#4289")
    assert harness["done"] and harness["done"][0].startswith(
        "https://github.com/RealLumenHere/x/pull/42 landed")
    committed = subprocess.run(["git", "-C", str(worktree), "show", "--name-only",
                                "--format=", "HEAD"], capture_output=True, text=True).stdout
    assert sorted(committed.split()) == ["docs/new.md", "docs/note.md"]
    unparsed = " ".join(e.get("raw", "") for e in events if e.get("type") == "unparsed")
    assert "argv-has-no-auto-commits=True" in unparsed
    assert "backend-base=http://127.0.0.1:9/v1" in unparsed
    assert getattr(state, "runtime", None) == "aider"


def test_exit0_without_changes_is_not_success(worktree, harness, monkeypatch):
    """aider exits 0 on backend errors; no changed file must never read as done."""
    state, exit_code, _, _, _, _ = _run(worktree, monkeypatch, "api_error")
    assert exit_code == 0
    assert classify(state, exit_code) == CRASH
    assert "changed no files" in state.result_text
    assert harness["lumen_pr"] == [] and harness["done"] == []


def test_aider_crash_skips_landing(worktree, harness, monkeypatch):
    state, exit_code, _, _, _, _ = _run(worktree, monkeypatch, "crash")
    assert exit_code == 3
    assert classify(state, exit_code) != SUCCESS
    assert harness["lumen_pr"] == [] and harness["done"] == []


def test_landing_refusal_is_not_success(worktree, harness, monkeypatch):
    def refuse(env, *args):
        raise RuntimeError("lumen-pr open failed (exit 1): PR body is missing")
    monkeypatch.setattr(fworker, "_lumen_pr", refuse)
    state, exit_code, _, _, _, _ = _run(worktree, monkeypatch, "edit")
    assert classify(state, exit_code) == CRASH
    assert "could not be landed" in state.result_text
    assert harness["done"] == []


def test_changed_paths_skip_aider_cache_and_follow_renames(worktree):
    subprocess.run(["git", "-C", str(worktree), "mv", "docs/note.md", "docs/renamed.md"],
                   check=True)
    (worktree / ".aider.tags.cache.v4").mkdir()
    (worktree / ".aider.tags.cache.v4" / "c").write_text("x")
    (worktree / ".aider.chat.history.md").write_text("x")
    (worktree / "b.txt").write_text("x")
    assert sorted(fworker.aider_changed_paths(str(worktree))) == ["b.txt", "docs/renamed.md"]


@pytest.mark.parametrize("branch,title,want", [
    ("docs/wq9-a-a1", "FOLLOW-UP wq#4237: Aider v3 docs.", "docs: aider v3 docs"),
    ("feat/wq9-a-a1", "wq#12 board: x " + "y" * 80, None),
    ("weird/wq9", "Fix the thing", "chore: fix the thing"),
    ("nobranch", "", "chore: apply ticket change"),
])
def test_commit_subject_passes_lumen_pr_lint(branch, title, want):
    s = fworker.aider_commit_subject(branch, title)
    assert re.match(SUBJECT_RE, s), s
    assert "wq#" not in s.lower() and "board:" not in s.lower() and not s.endswith(".")
    if want:
        assert s == want


def test_backend_env_reads_only_its_two_keys(tmp_path):
    f = tmp_path / "e"
    f.write_text("export OPENAI_API_BASE=\"http://h/v1\"\nOTHER=1\n# c\n")
    env = fworker.aider_backend_env({"A": "1"}, path=str(f))
    assert env == {"A": "1", "OPENAI_API_BASE": "http://h/v1"}


def test_runtime_selection(monkeypatch):
    class A:
        runtime = None
    monkeypatch.delenv("FOREMAN_RUNTIME", raising=False)
    assert fworker.runtime_of(A()) == "claude"
    monkeypatch.setenv("FOREMAN_RUNTIME", "aider")
    assert fworker.runtime_of(A()) == "aider"
    A.runtime = "claude"
    assert fworker.runtime_of(A()) == "claude"
    monkeypatch.setenv("FOREMAN_RUNTIME", "aidr")
    A.runtime = None
    with pytest.raises(ValueError):
        fworker.runtime_of(A())
