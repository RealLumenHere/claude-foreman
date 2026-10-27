"""wq#4735: repo-local path-scoped hash allowlist (.secret-scan-allow).

Token-shaped strings are assembled at runtime so this file is not itself a hit.
"""
import hashlib
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, "scan.py")
LINT = os.environ.get("CMD_SECRET_LINT", "/home/lumen/bin/cmd-secret-lint")
sys.path.insert(0, HERE)
import allow_local  # noqa: E402
import scan  # noqa: E402

GH = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"[:36]
GH2 = "gh" + "p_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"[:36]
GH_SHA = hashlib.sha256(GH.encode()).hexdigest()


def entry(sha=GH_SHA, glob="**/test_*.py"):
    return "sha256:%s %s\n" % (sha, glob)


# --- parser -----------------------------------------------------------------

def test_parse_valid_entry():
    assert allow_local.parse(entry()) == [(GH_SHA, "**/test_*.py")]


def test_parse_comments_and_blanks():
    assert allow_local.parse("# c\n\n" + entry() + "  # trailing\n") == [(GH_SHA, "**/test_*.py")]


def test_parse_malformed_grants_nothing():
    bad = ["sha256:%s\n" % GH_SHA,                      # no glob
           "sha256:abc123 **/test_*.py\n",              # short hash
           "sha256:%s **/test_*.py\n" % GH_SHA.upper(),  # uppercase hash
           "md5:%s **/test_*.py\n" % GH_SHA,            # wrong algo
           GH_SHA + " **/test_*.py\n"]                  # missing sha256: tag
    for b in bad:
        assert allow_local.parse(b) == [], b


def test_parse_non_fixture_glob_grants_nothing():
    assert allow_local.parse(entry(glob="**/*.py")) == []
    assert allow_local.parse(entry(glob="src/**")) == []
    assert allow_local.parse(entry(glob="**")) == []


def test_parse_oversize_grants_nothing():
    assert allow_local.parse(entry() * 2000) == []


# --- glob matching ----------------------------------------------------------

def test_glob_doublestar_crosses_dirs():
    assert allow_local.path_matches("**/test_*.py", "a/b/test_x.py")
    assert allow_local.path_matches("**/test_*.py", "test_x.py")
    assert allow_local.path_matches("**/fixtures/**", "pkg/fixtures/tok.json")


def test_glob_single_star_stays_in_segment():
    assert not allow_local.path_matches("tests/*.py", "tests/sub/x.py")
    assert allow_local.path_matches("tests/*.py", "tests/x.py")


def test_glob_rejects_production_path():
    assert not allow_local.path_matches("**/test_*.py", "src/prod.py")
    assert not allow_local.path_matches("**/fixtures/**", "src/latest_config.py")


# --- allowed() --------------------------------------------------------------

def test_allowed_fixture_hash_and_path():
    e = allow_local.parse(entry())
    assert allow_local.allowed("pkg/test_auth.py", GH, e)


def test_allowed_rejects_same_hash_on_production_path():
    e = allow_local.parse(entry())
    assert not allow_local.allowed("src/prod.py", GH, e)


def test_allowed_rejects_unlisted_value_in_fixture():
    e = allow_local.parse(entry())
    assert not allow_local.allowed("pkg/test_auth.py", GH2, e)


def test_allowed_fail_closed_on_empty():
    assert not allow_local.allowed("", GH, allow_local.parse(entry()))
    assert not allow_local.allowed(None, GH, allow_local.parse(entry()))
    assert not allow_local.allowed("pkg/test_auth.py", GH, ())


# --- scan.py integration ----------------------------------------------------

def test_scan_text_skips_allowed_fixture_token():
    e = tuple(allow_local.parse(entry()))
    assert scan.scan_text("x = %r\n" % GH, path="pkg/test_auth.py", local_allow=e) == []


def test_scan_text_still_flags_on_production_path():
    e = tuple(allow_local.parse(entry()))
    assert scan.scan_text("x = %r\n" % GH, path="src/prod.py", local_allow=e) != []


def test_scan_text_still_flags_unlisted_token_in_fixture():
    e = tuple(allow_local.parse(entry()))
    assert scan.scan_text("x = %r\n" % GH2, path="pkg/test_auth.py", local_allow=e) != []


def test_scan_text_without_path_never_allows():
    e = tuple(allow_local.parse(entry()))
    assert scan.scan_text("x = %r\n" % GH, local_allow=e) != []


# --- end to end: pre-receive hook -------------------------------------------

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


def commit(work, files, msg="c"):
    for name, text in files.items():
        p = work / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    run(work, "git", "add", "-A")
    run(work, "git", "commit", "-qm", msg)


def test_push_allowed_fixture_token_passes(repo):
    commit(repo, {".secret-scan-allow": entry(),
                  "pkg/test_auth.py": 'FAKE = "%s"\n' % GH})
    assert run(repo, "git", "push", "origin", "main", check=False).returncode == 0


def test_push_same_hash_on_production_path_refused(repo):
    commit(repo, {".secret-scan-allow": entry(),
                  "src/prod.py": 'TOK = "%s"\n' % GH})
    r = run(repo, "git", "push", "origin", "main", check=False)
    assert r.returncode != 0 and "src/prod.py" in r.stderr


def test_push_unlisted_token_in_fixture_refused(repo):
    commit(repo, {".secret-scan-allow": entry(),
                  "pkg/test_auth.py": 'FAKE = "%s"\n' % GH2})
    assert run(repo, "git", "push", "origin", "main", check=False).returncode != 0


def test_push_malformed_allowlist_refused(repo):
    commit(repo, {".secret-scan-allow": entry(glob="**"),  # non-fixture glob: grants nothing
                  "pkg/test_auth.py": 'FAKE = "%s"\n' % GH})
    assert run(repo, "git", "push", "origin", "main", check=False).returncode != 0


# --- cmd-secret-lint --------------------------------------------------------

LINT_ENV = dict(os.environ, SECRET_SCAN_LIB=HERE)


def lint(*files):
    return subprocess.run([sys.executable, LINT, *files], capture_output=True, text=True, env=LINT_ENV)


@pytest.mark.skipif(not os.path.exists(LINT), reason="cmd-secret-lint not installed")
def test_lint_allowed_fixture_token_passes(tmp_path):
    (tmp_path / ".secret-scan-allow").write_text(entry())
    f = tmp_path / "pkg" / "test_auth.py"
    f.parent.mkdir()
    f.write_text('FAKE = "%s"\n' % GH)
    assert lint(str(f)).returncode == 0


@pytest.mark.skipif(not os.path.exists(LINT), reason="cmd-secret-lint not installed")
def test_lint_same_hash_on_production_path_rejected(tmp_path):
    (tmp_path / ".secret-scan-allow").write_text(entry(glob="**/test_*.py"))
    f = tmp_path / "prod.py"
    f.write_text('TOK = "%s"\n' % GH)
    assert lint(str(f)).returncode == 1


@pytest.mark.skipif(not os.path.exists(LINT), reason="cmd-secret-lint not installed")
def test_lint_unlisted_token_in_fixture_rejected(tmp_path):
    (tmp_path / ".secret-scan-allow").write_text(entry())
    f = tmp_path / "test_auth.py"
    f.write_text('FAKE = "%s"\n' % GH2)
    assert lint(str(f)).returncode == 1


@pytest.mark.skipif(not os.path.exists(LINT), reason="cmd-secret-lint not installed")
def test_lint_stdin_never_allowlisted(tmp_path):
    (tmp_path / ".secret-scan-allow").write_text(entry())
    r = subprocess.run([sys.executable, LINT], input='x = "%s"\n' % GH,
                       capture_output=True, text=True, env=LINT_ENV, cwd=tmp_path)
    assert r.returncode == 1
