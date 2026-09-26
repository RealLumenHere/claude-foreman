"""wq#2753 (ISSUE-LOG #728): a citation entry carrying {"repo", "rev"} is checked
against that repo's own landed commit instead of the local worktree's diff — and every
leg of that check can fail (wq#2630: a probe that cannot fail proves nothing)."""
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import acceptance  # noqa: E402
import repo_sweep  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, check=True)


@pytest.fixture
def other_repo(tmp_path):
    """A managed repo with one landed commit on main touching tool.sh, and one
    unlanded commit on a side branch."""
    repo = tmp_path / "other"
    repo.mkdir()
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(repo)],
                   check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "tool.sh").write_text("".join(f"line {i}\n" for i in range(1, 11)))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "landed fix")
    landed = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("never merged\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unlanded")
    unlanded = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "main")
    return {"path": str(repo), "landed": landed, "unlanded": unlanded}


@pytest.fixture(autouse=True)
def managed(other_repo, monkeypatch):
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: {other_repo["path"]})


def check(entry):
    return acceptance._check_citation(entry, "/nonexistent-worktree", set())


def test_landed_cross_repo_citation_is_accepted(other_repo):
    ok, why, _ = check({"citation": "tool.sh:1-10",
                        "repo": other_repo["path"], "rev": other_repo["landed"]})
    assert ok, why
    assert other_repo["landed"] in why


def test_unlanded_rev_is_rejected(other_repo):
    ok, why, _ = check({"citation": "side.txt:1",
                        "repo": other_repo["path"], "rev": other_repo["unlanded"]})
    assert not ok
    assert "has not landed" in why


def test_unmanaged_repo_is_rejected(other_repo, monkeypatch):
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: set())
    ok, why, _ = check({"citation": "tool.sh:1",
                        "repo": other_repo["path"], "rev": other_repo["landed"]})
    assert not ok
    assert "not a managed repo" in why


def test_file_outside_the_revs_own_diff_is_rejected(other_repo):
    ok, why, _ = check({"citation": "base.txt:1",
                        "repo": other_repo["path"], "rev": other_repo["landed"]})
    assert not ok
    assert "not touched" in why


def test_line_range_beyond_the_committed_file_is_rejected(other_repo):
    ok, why, _ = check({"citation": "tool.sh:1-99",
                        "repo": other_repo["path"], "rev": other_repo["landed"]})
    assert not ok
    assert "cited 1-99" in why


def test_missing_rev_is_rejected(other_repo):
    ok, why, _ = check({"citation": "tool.sh:1", "repo": other_repo["path"]})
    assert not ok
    assert "needs a 'rev'" in why


def test_local_citations_still_use_the_worktree_diff(tmp_path):
    # no "repo" field: unchanged local path — nonexistent file in an empty worktree fails
    wt = tmp_path / "wt"
    wt.mkdir()
    ok, why, _ = acceptance._check_citation({"citation": "impl.py:1"}, str(wt), set())
    assert not ok
    assert "does not exist" in why
