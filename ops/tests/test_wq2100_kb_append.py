import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "wq2100-kb-append" / "kb-append"


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


def make_kb(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    git(kb, "init", "-q", "-b", "master")
    git(kb, "config", "user.email", "t@t")
    git(kb, "config", "user.name", "t")
    (kb / "LOG.md").write_text("1\n")
    git(kb, "add", "."); git(kb, "commit", "-qm", "base")
    return kb


def run(kb, branch):
    return subprocess.run([str(SCRIPT), branch, str(kb)], capture_output=True, text=True)


def test_lands_branch_after_master_moved_and_resets_backlog(tmp_path):
    kb = make_kb(tmp_path)
    wt = tmp_path / "wt"
    git(kb, "worktree", "add", "-q", "-b", "rows", str(wt))
    (wt / "LOG.md").write_text("1\n2\n")
    git(wt, "commit", "-qam", "row 2")
    (kb / "OTHER.md").write_text("x\n")
    git(kb, "add", "."); git(kb, "commit", "-qm", "handoff")
    (kb / "LOG.md").write_text("1\n2\n")  # primary backlog identical to branch content
    r = run(kb, "rows")
    assert r.returncode == 0, r.stderr
    assert (kb / "LOG.md").read_text() == "1\n2\n"
    assert git(kb, "status", "--porcelain") == ""
    assert "row 2" in git(kb, "log", "--format=%s")


def test_snapshots_unknown_backlog(tmp_path):
    kb = make_kb(tmp_path)
    wt = tmp_path / "wt"
    git(kb, "worktree", "add", "-q", "-b", "rows", str(wt))
    (wt / "NEW.md").write_text("n\n")
    git(wt, "add", "."); git(wt, "commit", "-qm", "new")
    (kb / "LOG.md").write_text("1\nbacklog\n")
    assert run(kb, "rows").returncode == 0
    assert "backlog" in (kb / "LOG.md").read_text()
    assert "snapshot" in git(kb, "log", "--format=%s")


def test_refuses_missing_branch(tmp_path):
    assert run(make_kb(tmp_path), "nope").returncode == 1
