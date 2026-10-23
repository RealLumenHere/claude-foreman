"""wq#4494 — reconcile_orphaned_worktrees.py against real git repos and real
tar/zstd round-trips in a tmp dir. No mocking of git, tar, or zstd: every
check this tool makes runs for real.
"""
import os
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reconcile_orphaned_worktrees as row  # noqa: E402


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def make_repo(tmp_path, name="origin-src"):
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


def add_worktree(repo, root, name, branch):
    os.makedirs(root, exist_ok=True)
    wt = os.path.join(root, name)
    git("worktree", "add", "-q", "-b", branch, wt, "main", cwd=repo)
    return wt


def orphan_it(repo, wt_name):
    """Delete the worktree's own admin dir under REPO/.git/worktrees/NAME —
    exactly what wq#4494 found: the `.git` FILE in the worktree still points
    there, but the admin dir itself is gone, so git can no longer vouch for
    the directory at all."""
    admin = os.path.join(str(repo), ".git", "worktrees", wt_name)
    assert os.path.isdir(admin), "admin dir must exist before we orphan it"
    import shutil
    shutil.rmtree(admin)


def wq_db(tmp_path, monkeypatch, rows):
    db = tmp_path / "queue.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT)")
    conn.executemany("INSERT INTO tasks(id, status) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(row, "WQ_DB", str(db))


# ── is_orphaned ──────────────────────────────────────────────────────────────

def test_registered_worktree_is_not_orphaned(tmp_path):
    repo = make_repo(tmp_path)
    wt = add_worktree(repo, tmp_path / "wts", "wq1-a1", "wq1-a1")
    orphaned, reason = row.is_orphaned(wt)
    assert orphaned is False
    assert "still present" in reason


def test_worktree_with_admin_dir_removed_is_orphaned(tmp_path):
    repo = make_repo(tmp_path)
    wt = add_worktree(repo, tmp_path / "wts", "wq1-a1", "wq1-a1")
    orphan_it(repo, "wq1-a1")
    orphaned, reason = row.is_orphaned(wt)
    assert orphaned is True
    assert "missing" in reason


def test_plain_dir_with_no_git_file_is_not_orphaned(tmp_path):
    plain = tmp_path / "repo-verify-main"
    plain.mkdir()
    orphaned, reason = row.is_orphaned(str(plain))
    assert orphaned is False
    assert "not a linked-worktree" in reason


# ── classify ─────────────────────────────────────────────────────────────────

def test_done_ticket_is_archivable(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wt = add_worktree(repo, tmp_path / "wts", "wq1234-a1", "wq1234-a1")
    orphan_it(repo, "wq1234-a1")
    wq_db(tmp_path, monkeypatch, [(1234, "done")])
    action, reason = row.classify(wt)
    assert action == "archive"
    assert "wq#1234 done" in reason


def test_claimed_ticket_is_skipped(tmp_path, monkeypatch):
    """The boundary this fixes must be able to fail: a ticket that is still
    claimed (not done) must never be archived, even though its directory is
    administratively orphaned exactly like a done one's."""
    repo = make_repo(tmp_path)
    wt = add_worktree(repo, tmp_path / "wts", "wq9999-a1", "wq9999-a1")
    orphan_it(repo, "wq9999-a1")
    wq_db(tmp_path, monkeypatch, [(9999, "claimed")])
    action, reason = row.classify(wt)
    assert action == "skip"
    assert "not done" in reason


def test_ticket_not_in_db_fails_closed(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wt = add_worktree(repo, tmp_path / "wts", "wq555-a1", "wq555-a1")
    orphan_it(repo, "wq555-a1")
    wq_db(tmp_path, monkeypatch, [])
    action, reason = row.classify(wt)
    assert action == "skip"
    assert "fail closed" in reason


def test_name_without_ticket_id_needs_manual_review(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wt = add_worktree(repo, tmp_path / "wts", "repo-verify-main", "repo-verify-main")
    orphan_it(repo, "repo-verify-main")
    wq_db(tmp_path, monkeypatch, [])
    action, reason = row.classify(wt)
    assert action == "skip"
    assert "manual review" in reason


# ── archive_dir ──────────────────────────────────────────────────────────────

def test_archive_round_trips_and_verifies(tmp_path):
    src = tmp_path / "payload"
    src.mkdir()
    (src / "a.txt").write_text("hello\n")
    sub = src / "nested"
    sub.mkdir()
    (sub / "b.txt").write_text("world\n")

    archive_root = tmp_path / "archive"
    archive_path, err = row.archive_dir(str(src), str(archive_root))
    assert err is None
    assert os.path.isfile(archive_path)
    assert archive_path.endswith(".tar.zst")


def test_full_run_apply_archives_and_removes_done_ticket(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    root = tmp_path / "worktrees"
    wt = add_worktree(repo, root, "wq42-a1", "wq42-a1")
    (open(os.path.join(wt, "extra.txt"), "w")).write("payload\n")
    orphan_it(repo, "wq42-a1")
    wq_db(tmp_path, monkeypatch, [(42, "done")])

    archive_root = tmp_path / "archive"
    error, results = row.run(str(root), apply=True, archive_root=str(archive_root))
    assert error is None
    [rec] = [r for r in results if r["path"] == wt]
    assert rec["action"] == "archive"
    assert rec["removed"] is True
    assert not os.path.exists(wt)
    assert os.path.isfile(rec["archived_to"])


def test_full_run_dry_run_leaves_directory_in_place(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    root = tmp_path / "worktrees"
    wt = add_worktree(repo, root, "wq77-a1", "wq77-a1")
    orphan_it(repo, "wq77-a1")
    wq_db(tmp_path, monkeypatch, [(77, "done")])

    error, results = row.run(str(root), apply=False, archive_root=str(tmp_path / "archive"))
    assert error is None
    [rec] = [r for r in results if r["path"] == wt]
    assert rec["action"] == "archive"
    assert "removed" not in rec
    assert os.path.exists(wt)
