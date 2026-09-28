"""wq#2981: does the pre-commit gate actually refuse a commit that stages a file
carrying a whole credential shape -- the missing half of ISSUE-LOG #951?
test_the_mask_suite_does_not_arm_its_own_tripwire (harness/tests/test_masking.py)
only sees a bad file AFTER it lands on main; scan_tree.py --staged runs the identical
walk against the git INDEX, and this pre-commit hook wires that in so `git commit`
refuses before the blob ever reaches history.

Builds a throwaway git repo under tmp_path with a copy of the real harness/masking.py
and this bundle's scan_tree.py/pre-commit, so the walk under test is the real one, not
a stand-in. wq#2630: the bad-fixture case is the probe that must be able to fail -- if
the hook were a no-op, `git commit` would exit 0 on a whole credential shape and this
test would catch it.
"""
import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
BUNDLE = os.path.dirname(os.path.abspath(__file__))

# wq#2792-style dummy: built at runtime so this FILE's own source never holds the
# contiguous shape (test_the_mask_suite_does_not_arm_its_own_tripwire would refuse it
# on the same grounds this test exercises). Written only into a throwaway scratch repo
# below, never committed to this repo.
_AWS_BAD = "AKIA" + "ABCDEFGHIJKLMNOP"
# Same value, split the way harness/tests/fixtures/wq2913_values.py splits sk-key
# dummies -- adjacent string literals, byte-identical once the file is parsed, but no
# contiguous 16-char run in the SOURCE text a scan reads.
_AWS_GOOD_SOURCE = 'value = "AKIA" "ABCDEFGHIJKLM" "NOP"\n'


def _run(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


@pytest.fixture
def scratch_repo(tmp_path):
    repo = tmp_path / "scratch"
    repo.mkdir()
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "test"], cwd=repo)

    harness = repo / "harness"
    harness.mkdir()
    shutil.copy(os.path.join(REPO_ROOT, "harness", "masking.py"), harness / "masking.py")
    live_code = os.path.join(REPO_ROOT, "harness", "live_code.py")
    if os.path.exists(live_code):
        shutil.copy(live_code, harness / "live_code.py")

    bundle = repo / "ops" / "wq2739-tripwire-corpus"
    bundle.mkdir(parents=True)
    shutil.copy(os.path.join(BUNDLE, "scan_tree.py"), bundle / "scan_tree.py")
    hook_src = os.path.join(BUNDLE, "pre-commit")
    hook_dest = repo / ".git" / "hooks" / "pre-commit"
    shutil.copy(hook_src, hook_dest)
    os.chmod(hook_dest, 0o755)

    readme = repo / "README.md"
    readme.write_text("scratch\n")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "init"], cwd=repo)
    return repo


def test_hook_refuses_a_staged_whole_credential_shape(scratch_repo):
    bad = scratch_repo / "secret.py"
    bad.write_text("value = %r\n" % _AWS_BAD)
    _run(["git", "add", "secret.py"], cwd=scratch_repo)

    result = _run(["git", "commit", "-q", "-m", "add secret"], cwd=scratch_repo)

    assert result.returncode != 0, (
        "pre-commit hook let a whole credential shape land -- it is a no-op")
    assert "secret.py" in result.stdout + result.stderr
    log = _run(["git", "log", "--oneline"], cwd=scratch_repo).stdout
    assert "add secret" not in log, "commit was refused but landed anyway"


def test_hook_allows_the_same_value_split_at_the_seam(scratch_repo):
    good = scratch_repo / "secret.py"
    good.write_text(_AWS_GOOD_SOURCE)
    _run(["git", "add", "secret.py"], cwd=scratch_repo)

    result = _run(["git", "commit", "-q", "-m", "add split value"], cwd=scratch_repo)

    assert result.returncode == 0, "hook refused a split value: %s" % (result.stderr,)
    log = _run(["git", "log", "--oneline"], cwd=scratch_repo).stdout
    assert "add split value" in log


def test_hook_is_a_noop_outside_the_mask_suite(tmp_path):
    """A repo with no harness/masking.py must not be gated by a hook it never asked for."""
    repo = tmp_path / "plain"
    repo.mkdir()
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "test"], cwd=repo)
    hooks = repo / ".git" / "hooks"
    shutil.copy(os.path.join(BUNDLE, "pre-commit"), hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)

    (repo / "secret.py").write_text("value = %r\n" % _AWS_BAD)
    _run(["git", "add", "secret.py"], cwd=repo)
    result = _run(["git", "commit", "-q", "-m", "add secret, no mask suite here"], cwd=repo)

    assert result.returncode == 0, "hook fired in a repo with no harness/masking.py"


def test_install_hook_refuses_to_clobber_a_foreign_hook(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()
    _run(["git", "init", "-q"], cwd=repo)
    hooks = repo / ".git" / "hooks"
    foreign = hooks / "pre-commit"
    foreign.write_text("#!/bin/bash\necho not ours\n")
    os.chmod(foreign, 0o755)

    result = subprocess.run(
        ["bash", os.path.join(BUNDLE, "install.sh"), "--install-hook", str(repo)],
        capture_output=True, text=True)

    assert result.returncode != 0
    assert "echo not ours" == foreign.read_text().splitlines()[1]


def test_install_hook_writes_our_hook_on_a_clean_repo(tmp_path):
    repo = tmp_path / "target2"
    repo.mkdir()
    _run(["git", "init", "-q"], cwd=repo)

    result = subprocess.run(
        ["bash", os.path.join(BUNDLE, "install.sh"), "--install-hook", str(repo)],
        capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    dest = repo / ".git" / "hooks" / "pre-commit"
    assert dest.exists()
    assert "wq#2981" in dest.read_text()
