"""wq#1141: make_worktree must serialize its PRIMARY-.git writes on the same slock scope
`lumen-pr land` takes around a merge.

The wq#1097 corruption came from two unsynchronized git processes over one .git — an
fworker running `worktree prune/add` while an orchestrator rebased the same primary tree.
Two properties carry the fix, and both are tested here against a real slock and a scratch
queue DB (never the live one):

  1. the scope fworker derives is BYTE-IDENTICAL to lumen-pr's for the same origin —
     locking a different string would serialize nothing, so this is cross-checked against
     `lumen-pr gitlock <repo> scope` itself rather than against a hardcoded expectation;
  2. a held lock defers instead of racing: make_worktree raises WorktreeLockUnavailable,
     touches no worktree, and `fworker run` turns that into DEFERRED + a released claim.
"""
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
sys.path.insert(0, TESTS_DIR)
import fworker  # noqa: E402
from test_fworker_integration import (  # noqa: E402 — same scratch-repo/env fixtures
    make_scratch_env, make_scratch_repo, wq_add, wq_status,
)

FWORKER = os.path.join(HARNESS_DIR, "fworker.py")
SLOCK = "/home/lumen/bin/slock"
LUMEN_PR = "/home/lumen/bin/lumen-pr"


def repo_with_origin(tmp_path, name, url):
    repo = str(tmp_path / name)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "remote", "add", "origin", url], check=True)
    return repo


def slock(env, *args):
    return subprocess.run([SLOCK, *args], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("url", [
    "https://github.com/lumen-industries/lumen-plays-pokemon.git",   # -> lpp-git alias
    "https://github.com/lumen-industries/foreman.git",
    "git@github.com:lumen-industries/foreman.git",                   # scp-form
    "http://lumen@100.121.17.5:3000/lumen/lumen-bin.git",            # gitea, userinfo + port
])
def test_scope_matches_lumen_pr(tmp_path, url):
    """The equality the whole fix rests on. lumen-pr is the reference implementation."""
    repo = repo_with_origin(tmp_path, "r", url)
    p = subprocess.run([LUMEN_PR, "gitlock", repo, "scope"], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert fworker.repo_git_lock_scope(repo) == p.stdout.strip()


def test_scope_for_lpp_is_the_established_alias(tmp_path):
    repo = repo_with_origin(tmp_path, "r",
                            "https://github.com/lumen-industries/lumen-plays-pokemon.git")
    assert fworker.repo_git_lock_scope(repo) == "lpp-git"


def test_scope_without_origin_is_deterministic(tmp_path):
    """No origin is the scratch-repo case: lumen-pr refuses such a repo, so nothing has to
    match — but two fworkers on the same repo must still land on the same scope."""
    repo = make_scratch_repo(tmp_path)
    assert fworker.repo_git_lock_scope(repo) == "scratch_repo-git"


def test_make_worktree_takes_and_releases_the_lock(tmp_path, monkeypatch):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, *_ = make_scratch_env(tmp_path, 0, "success")
    monkeypatch.setenv("WQ_DB", wq_db)
    monkeypatch.setattr(fworker, "WORKTREES_ROOT", str(tmp_path / "worktrees"))
    scope = fworker.repo_git_lock_scope(repo)

    wt, branch = fworker.make_worktree(repo, 4242, 1, "lock happy path", "mechanical-edit")

    assert os.path.isdir(wt)
    head = subprocess.run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True)
    assert head.stdout.strip() == branch
    # Released in the finally — a setup that parks the repo lock would block every land.
    assert "(no active locks)" in slock(dict(os.environ), "who", scope).stdout


def test_make_worktree_defers_when_another_holder_has_the_lock(tmp_path, monkeypatch):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, *_ = make_scratch_env(tmp_path, 0, "success")
    monkeypatch.setenv("WQ_DB", wq_db)
    monkeypatch.setattr(fworker, "WORKTREES_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setattr(fworker, "WORKTREE_LOCK_WAIT_S", 1)
    monkeypatch.setattr(fworker, "WORKTREE_LOCK_POLL_S", 1)
    scope = fworker.repo_git_lock_scope(repo)
    assert slock(dict(os.environ), "acquire", scope, "--ttl", "5",
                 "--holder", "orchestrator-mid-land").returncode == 0

    with pytest.raises(fworker.WorktreeLockUnavailable) as e:
        fworker.make_worktree(repo, 4243, 1, "lock contended", "mechanical-edit")

    assert scope in str(e.value)
    # Nothing half-created: the deferral must leave no worktree and no branch behind.
    assert not os.path.exists(os.path.join(str(tmp_path / "worktrees"), "wq4243-a1"))
    branches = subprocess.run(["git", "-C", repo, "branch", "--list", "*wq4243*"],
                              capture_output=True, text=True).stdout
    assert branches.strip() == ""
    # Someone else's lock is left strictly alone.
    assert "orchestrator-mid-land" in slock(dict(os.environ), "who", scope).stdout
    slock(dict(os.environ), "release", scope, "--holder", "orchestrator-mid-land")


def test_lock_fails_closed_when_slock_is_unusable(tmp_path, monkeypatch):
    """A fence that fails open is not a fence: if slock cannot run at all we cannot prove
    serialization, so setup defers rather than racing the primary .git unprotected."""
    repo = make_scratch_repo(tmp_path)
    monkeypatch.setattr(fworker, "WORKTREES_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setattr(fworker, "WORKTREE_LOCK_WAIT_S", 0)
    monkeypatch.setattr(fworker, "SLOCK_BIN", str(tmp_path / "no-such-slock"))

    with pytest.raises(fworker.WorktreeLockUnavailable):
        fworker.make_worktree(repo, 4244, 1, "no slock", "mechanical-edit")
    assert not os.path.exists(os.path.join(str(tmp_path / "worktrees"), "wq4244-a1"))


def test_fworker_run_defers_and_releases_the_claim(tmp_path):
    """End-to-end: contention with a live land is routine traffic, so the ticket comes back
    open for the next dispatch pass instead of crashing or burning an attempt."""
    repo = make_scratch_repo(tmp_path)
    env, wq_db, _events, _runs, worktrees_root, _state = make_scratch_env(tmp_path, 0, "success")
    env["FOREMAN_WORKTREE_LOCK_WAIT_S"] = "1"
    env["FOREMAN_WORKTREE_LOCK_POLL_S"] = "1"
    ticket_id = wq_add(env, "deferred by repo lock")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    scope = fworker.repo_git_lock_scope(repo)
    assert slock(env, "acquire", scope, "--ttl", "5",
                 "--holder", "orchestrator-mid-land").returncode == 0
    try:
        p = subprocess.run(
            [sys.executable, FWORKER, "run", "--ticket", str(ticket_id), "--repo", repo,
             "--archetype", "mechanical-edit", "--claude-bin",
             os.path.join(TESTS_DIR, "fake_claude.py")],
            capture_output=True, text=True, env=env, timeout=180)
    finally:
        slock(env, "release", scope, "--holder", "orchestrator-mid-land")

    assert p.returncode == fworker.EXIT_DEFERRED, p.stdout + p.stderr
    assert "DEFERRED" in p.stdout
    assert wq_status(env, ticket_id) == "open"
    assert not os.path.exists(os.path.join(worktrees_root, f"wq{ticket_id}-a1"))
