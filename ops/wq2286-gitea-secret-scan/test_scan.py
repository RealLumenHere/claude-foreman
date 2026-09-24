"""wq#2286: the pre-receive scan refuses token-shaped pushes and leaves normal ones alone.

Token-shaped strings are assembled at runtime so this file is not itself a hit.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, "scan.py")
sys.path.insert(0, HERE)
import scan  # noqa: E402

GH = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"[:36]
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"


def run(cwd, *args, env=None, check=True):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
             GIT_COMMITTER_EMAIL="t@t", SECRET_SCAN_ALLOW=os.path.join(HERE, "no-such-allow.txt"))
    e.update(env or {})
    return subprocess.run(args, cwd=cwd, env=e, capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    bare = tmp_path / "srv.git"
    run(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(bare))
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexec %s %s pre-receive\n" % (sys.executable, SCAN))
    hook.chmod(0o755)
    work = tmp_path / "work"
    run(tmp_path, "git", "clone", "-q", str(bare), str(work))
    (work / "ok.txt").write_text("nothing here\n")
    run(work, "git", "add", "-A")
    run(work, "git", "commit", "-qm", "base")
    run(work, "git", "push", "-q", "origin", "main")
    return work


def commit(work, name, text, msg="c"):
    (work / name).write_text(text)
    run(work, "git", "add", "-A")
    run(work, "git", "commit", "-qm", msg)


def push(work, ref="main"):
    return run(work, "git", "push", "origin", ref, check=False)


def test_normal_push_is_accepted(repo):
    commit(repo, "a.py", "print('hi')\n")
    assert push(repo).returncode == 0


def test_github_token_is_refused_and_value_not_echoed(repo):
    commit(repo, "cfg.txt", "token = %s\n" % GH)
    r = push(repo)
    assert r.returncode != 0
    assert "github-token" in r.stderr and "cfg.txt:1" in r.stderr
    assert GH not in r.stderr and GH not in r.stdout


def test_private_key_block_is_refused(repo):
    commit(repo, "k.pem", PEM + "\nabc\n")
    assert push(repo).returncode != 0


def test_secret_in_an_earlier_commit_of_the_push_is_refused(repo):
    commit(repo, "cfg.txt", "%s\n" % GH)
    commit(repo, "cfg.txt", "removed\n", "remove it")
    assert push(repo).returncode != 0


def test_secret_in_a_commit_message_is_refused(repo):
    commit(repo, "a.txt", "x\n", "oops %s" % GH)
    assert push(repo).returncode != 0


def test_new_branch_is_scanned(repo):
    run(repo, "git", "checkout", "-q", "-b", "feature")
    commit(repo, "cfg.txt", "%s\n" % GH)
    assert push(repo, "feature").returncode != 0


def test_inline_allow_marker(repo):
    commit(repo, "t.py", 'FAKE = "%s"  # secret-scan:allow\n' % GH)
    assert push(repo).returncode == 0


def test_allow_file_by_sha(repo, tmp_path):
    import hashlib
    allow = tmp_path / "allow.txt"
    allow.write_text("# fake\n%s\n" % hashlib.sha256(GH.encode()).hexdigest()[:12])
    commit(repo, "t.py", 'FAKE = "%s"\n' % GH)
    r = run(repo, "git", "push", "origin", "main", check=False, env={"SECRET_SCAN_ALLOW": str(allow)})
    assert r.returncode == 0


def test_ref_delete_is_not_scanned(repo):
    run(repo, "git", "push", "-q", "origin", "main:refs/heads/tmp")
    assert run(repo, "git", "push", "origin", ":tmp", check=False).returncode == 0


def test_short_or_wordy_lookalikes_pass():
    assert scan.scan_text("ghp_notes and highs_solver ghs_short xghp_" + "A" * 40) == []


def test_fails_open_on_scanner_error(tmp_path):
    bare = tmp_path / "x.git"
    run(tmp_path, "git", "init", "-q", "--bare", str(bare))
    r = subprocess.run([sys.executable, SCAN, "pre-receive"], input=("0" * 40 + " " + "1" * 40 + " refs/heads/m\n"),
                       cwd=bare, capture_output=True, text=True, env=dict(os.environ, GIT_DIR=str(bare)))
    assert r.returncode == 0 and "not scanned" in r.stderr


def test_audit_reports_without_values(repo, tmp_path):
    commit(repo, "cfg.txt", "%s\n" % GH)
    bare = str(tmp_path / "srv.git")
    run(repo, "git", "push", "-q", "-f", "origin", "main", env={"SECRET_SCAN_ALLOW": "/nonexistent"}, check=False)
    # the hook refused, so put the object in directly to audit it
    run(repo, "git", "--git-dir", bare, "fetch", "-q", str(repo / ".git"), "main:refs/heads/aud")
    r = subprocess.run([sys.executable, SCAN, "audit", bare], capture_output=True, text=True,
                       env=dict(os.environ, SECRET_SCAN_ALLOW="/nonexistent"))
    assert r.returncode == 1 and "github-token" in r.stdout and GH not in r.stdout
