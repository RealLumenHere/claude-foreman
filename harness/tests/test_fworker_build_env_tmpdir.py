"""fworker.build_env's TMPDIR isolation (wq#1915(B)).

wq#1852 (2 attempts) and wq#1889 (2 attempts) both died CAPABILITY_DENIED writing a
log/test file under the system temp dir -- guard.sh refuses any write outside the
worktree, and every default-tempfile-location tool (tempfile.gettempdir(), bash's
mktemp, etc.) falls back to /tmp unless TMPDIR/TEMP/TMP say otherwise. build_env must
set all three to a directory INSIDE the worktree, create it 0700, and keep it out of
git status by adding it to the repo's (shared) info/exclude.
"""
import os
import stat
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, check=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "base.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_build_env_points_tmpdir_temp_tmp_inside_the_worktree(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(fworker, "read_oauth_token", lambda: "tok")
    env = fworker.build_env("w1", "sonnet-5", "high", str(repo), 9999)
    want = os.path.join(str(repo), ".tmp")
    assert env["TMPDIR"] == want
    assert env["TEMP"] == want
    assert env["TMP"] == want


def test_build_env_creates_the_tmp_dir_mode_0700(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(fworker, "read_oauth_token", lambda: "tok")
    fworker.build_env("w1", "sonnet-5", "high", str(repo), 9999)
    tmp_dir = os.path.join(str(repo), ".tmp")
    assert os.path.isdir(tmp_dir)
    mode = stat.S_IMODE(os.stat(tmp_dir).st_mode)
    assert mode == 0o700


def test_build_env_excludes_tmp_via_git_info_exclude_not_gitignore(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(fworker, "read_oauth_token", lambda: "tok")
    fworker.build_env("w1", "sonnet-5", "high", str(repo), 9999)
    p = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"],
                       capture_output=True, text=True, check=True, cwd=str(repo))
    exclude_path = os.path.normpath(os.path.join(str(repo), p.stdout.strip()))
    assert os.path.exists(exclude_path)
    with open(exclude_path) as f:
        lines = f.read().splitlines()
    assert fworker.TMP_EXCLUDE_PATTERN in lines
    # never a tracked .gitignore line -- landing this fix must not itself need a PR
    # touching the worker's own worktree content.
    assert not os.path.exists(os.path.join(str(repo), ".gitignore"))
    status = _git(repo, "status", "--porcelain").stdout
    assert ".tmp" not in status


def test_build_env_tmp_exclude_is_idempotent_across_repeat_calls(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(fworker, "read_oauth_token", lambda: "tok")
    fworker.build_env("w1", "sonnet-5", "high", str(repo), 9999)
    fworker.build_env("w2", "sonnet-5", "high", str(repo), 9999)
    p = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"],
                       capture_output=True, text=True, check=True, cwd=str(repo))
    exclude_path = os.path.normpath(os.path.join(str(repo), p.stdout.strip()))
    with open(exclude_path) as f:
        lines = f.read().splitlines()
    assert lines.count(fworker.TMP_EXCLUDE_PATTERN) == 1


def test_build_env_tmp_dir_survives_a_pre_existing_looser_mode(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(fworker, "read_oauth_token", lambda: "tok")
    tmp_dir = os.path.join(str(repo), ".tmp")
    os.makedirs(tmp_dir, mode=0o777, exist_ok=True)
    os.chmod(tmp_dir, 0o777)
    fworker.build_env("w1", "sonnet-5", "high", str(repo), 9999)
    assert stat.S_IMODE(os.stat(tmp_dir).st_mode) == 0o700


def test_a_tool_defaulting_to_tempfile_gettempdir_lands_under_the_worktree(tmp_path, monkeypatch):
    """The actual point of the fix: a child process that never heard of FOREMAN_* env
    vars, only TMPDIR, ends up writing inside the worktree."""
    repo = _repo(tmp_path)
    monkeypatch.setattr(fworker, "read_oauth_token", lambda: "tok")
    env = fworker.build_env("w1", "sonnet-5", "high", str(repo), 9999)
    p = subprocess.run(
        [sys.executable, "-c",
         "import tempfile; print(tempfile.gettempdir())"],
        capture_output=True, text=True, env=env, check=True)
    assert p.stdout.strip() == os.path.join(str(repo), ".tmp")
