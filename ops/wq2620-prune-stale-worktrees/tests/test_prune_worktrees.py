"""wq#2620 — prune_worktrees.py against real git repos built in a tmp dir. No
mocking of git itself: every check the tool makes (merge-base, status, worktree
list) runs for real against throwaway repos, so a change to git's own output
shapes would fail these tests too.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prune_worktrees as pw  # noqa: E402


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def make_repo(tmp_path, name="origin-src"):
    """A bare 'origin' plus a working clone on 'main', HEAD pointed at it."""
    bare = tmp_path / f"{name}.git"
    work = tmp_path / name
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    git("config", "user.email", "t@example.com", cwd=work)
    git("config", "user.name", "T", cwd=work)
    (work / "f.txt").write_text("1\n")
    git("add", "f.txt", cwd=work)
    git("commit", "-q", "-m", "init", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    return work


def add_worktree(repo, root, name, branch, ahead=False, dirty=False, base_branch="main"):
    """Cut a linked worktree at root/name on a new branch off `base_branch`."""
    os.makedirs(root, exist_ok=True)
    wt = os.path.join(root, name)
    git("worktree", "add", "-q", "-b", branch, wt, base_branch, cwd=repo)
    if ahead:
        with open(os.path.join(wt, "g.txt"), "w") as f:
            f.write("x\n")
        git("add", "g.txt", cwd=wt)
        git("commit", "-q", "-m", "unique work", cwd=wt)
    if dirty:
        with open(os.path.join(wt, "f.txt"), "a") as f:
            f.write("uncommitted\n")
    return wt


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def test_merged_clean_worktree_is_prunable(tmp_path, repo):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9001-a1", "feat/wq9001-a1-done")
    results = pw.run([str(root)], apply=False, fetch=False)
    assert len(results) == 1
    assert results[0]["action"] == "prune"


def test_branch_with_unique_commits_is_skipped(tmp_path, repo):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9002-a1", "feat/wq9002-a1-wip", ahead=True)
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip"
    assert "commits not in" in r["reason"]


def test_dirty_worktree_is_skipped_even_if_merged(tmp_path, repo):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9003-a1", "feat/wq9003-a1-dirty", dirty=True)
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip"
    assert "uncommitted" in r["reason"]


def test_locked_worktree_is_skipped(tmp_path, repo):
    root = tmp_path / "worktrees"
    wt = add_worktree(repo, root, "wq9004-a1", "feat/wq9004-a1-locked")
    git("worktree", "lock", wt, cwd=repo)
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip"
    assert "locked" in r["reason"]


def test_stray_directory_without_gitfile_is_ignored(tmp_path):
    root = tmp_path / "worktrees"
    os.makedirs(root / "not-a-worktree")
    assert pw.run([str(root)], apply=False, fetch=False) == []


def test_primary_checkout_is_never_a_candidate(tmp_path, repo):
    """A full clone's .git is a DIRECTORY, not a linked-worktree file — must never
    be picked up even if someone points --roots straight at a parent containing one."""
    assert pw.linked_worktree_repo(str(repo)) is None


def test_live_wq_claim_blocks_pruning(tmp_path, repo, monkeypatch):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9005-a1", "feat/wq9005-a1-live")
    db = tmp_path / "queue.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT, lease_expires TEXT)")
    conn.execute("INSERT INTO tasks(id, status, lease_expires) VALUES (9005, 'claimed', NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip"
    assert "live wq claim" in r["reason"]


def test_expired_lease_does_not_block_pruning(tmp_path, repo, monkeypatch):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9006-a1", "feat/wq9006-a1-expired")
    db = tmp_path / "queue.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT, lease_expires TEXT)")
    conn.execute("INSERT INTO tasks(id, status, lease_expires) VALUES (9006, 'claimed', '2000-01-01T00:00:00Z')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "prune"


def test_missing_wq_db_fails_closed(tmp_path, repo, monkeypatch):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9007-a1", "feat/wq9007-a1-nodb")
    monkeypatch.setattr(pw, "WQ_DB", str(tmp_path / "does-not-exist.db"))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip"
    assert "fail closed" in r["reason"]


def test_live_slock_blocks_scratch_worktree(tmp_path, repo, monkeypatch):
    root = tmp_path / "scratch" / "worktrees"
    wt_name = "myrepo-feat-wq9008-thing"
    add_worktree(repo, root, wt_name, "feat/wq9008-thing")
    owners = root / ".owners"
    os.makedirs(owners)
    (owners / wt_name).write_text("agent=some-session-1\ncreated=now\n")
    db = tmp_path / "queue.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE slocks(scope TEXT, holder TEXT, expires_at TEXT)")
    conn.execute("CREATE TABLE slocks_ro(scope TEXT, holder TEXT, expires_at TEXT)")
    conn.execute("INSERT INTO slocks(scope, holder, expires_at) VALUES ('lane:x', 'some-session-1', NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip"
    assert "live slock" in r["reason"]


def test_scratch_worktree_with_no_owner_record_is_prunable(tmp_path, repo, monkeypatch):
    root = tmp_path / "scratch" / "worktrees"
    add_worktree(repo, root, "myrepo-feat-orphan", "feat/orphan")
    monkeypatch.setattr(pw, "WQ_DB", str(tmp_path / "does-not-exist.db"))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "prune"
    assert "no owner record" in r["reason"]


def test_apply_actually_removes_and_dry_run_does_not(tmp_path, repo):
    root = tmp_path / "worktrees"
    wt = add_worktree(repo, root, "wq9009-a1", "feat/wq9009-a1-done")

    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "prune"
    assert r["bytes"] > 0
    assert os.path.isdir(wt), "dry-run must never remove anything"

    [r] = pw.run([str(root)], apply=True, fetch=False)
    assert r["action"] == "prune"
    assert r["removed"] is True
    assert r["bytes"] > 0, "size must be measured before removal, not after"
    assert not os.path.isdir(wt)


def test_full_cli_dry_run_reports_json(tmp_path, repo, capsys):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9010-a1", "feat/wq9010-a1-done")
    log = tmp_path / "log.jsonl"
    rc = pw.main(["--roots", str(root), "--no-fetch", "--json", "--log", str(log)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["prunable"] == 1
    assert out["summary"]["mode"] == "dry-run"
    assert log.exists()
    logged = json.loads(log.read_text().strip().splitlines()[-1])
    assert logged["summary"]["prunable"] == 1
