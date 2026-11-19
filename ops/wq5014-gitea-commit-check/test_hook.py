"""wq#5014: push-level tests for the fail-closed commit-check pre-receive hook.

Each test builds a bare repo whose hooks/pre-receive runs hook.py with LUMEN_CCHK_LIB/ETC
pointed at a temp install (the pinned wheel unzipped, this bundle's cchk.toml), then pushes
real commits from a clone. Needs the wheel: set CCHK_WHEEL, or drop it next to this file
(README deploy step downloads it). Skipped when neither is present.
"""
import hashlib
import os
import shutil
import subprocess
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WHEEL_NAME = "commit_check-2.22.0-py3-none-any.whl"
WHEEL_SHA256 = "0f50e5c065a19b5d2d1b3672e744a0aea298922b3facbe92fbc0660f64bc7b9d"
WHEEL = os.environ.get("CCHK_WHEEL") or os.path.join(HERE, WHEEL_NAME)

pytestmark = pytest.mark.skipif(not os.path.isfile(WHEEL), reason="commit-check wheel not present")

ASSIST = "Assisted-by: Claude (Anthropic) via Lumen Industries automation"


def run(cwd, *args, env=None, check=True):
    p = subprocess.run(list(args), cwd=str(cwd), capture_output=True, text=True, env=env)
    if check:
        assert p.returncode == 0, p.stdout + p.stderr
    return p


@pytest.fixture
def forge(tmp_path):
    with open(WHEEL, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == WHEEL_SHA256
    lib, etc = tmp_path / "lib", tmp_path / "etc"
    lib.mkdir(), etc.mkdir()
    zipfile.ZipFile(WHEEL).extractall(lib / "site")
    shutil.copy(os.path.join(HERE, "hook.py"), lib / "hook.py")
    shutil.copy(os.path.join(HERE, "cchk.toml"), etc / "cchk.toml")
    bare = tmp_path / "repo.git"
    run(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(bare))
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexec env LUMEN_CCHK_LIB=%s LUMEN_CCHK_ETC=%s /usr/bin/python3 "
                    "-I %s/hook.py\n" % (lib, etc, lib))
    hook.chmod(0o755)
    wc = tmp_path / "wc"
    run(tmp_path, "git", "clone", "-q", str(bare), str(wc))
    run(wc, "git", "config", "user.email", "lumen@lumennode.uk")
    run(wc, "git", "config", "user.name", "Lumen Industries")
    run(wc, "git", "checkout", "-q", "-b", "main")
    # seed main through the hook with a compliant commit
    commit(wc, "chore(repo): seed\n\n" + ASSIST)
    assert push(wc).returncode == 0
    return {"wc": wc, "lib": lib, "etc": etc, "bare": bare}


_n = [0]


def commit(wc, msg):
    _n[0] += 1
    (wc / f"f{_n[0]}.txt").write_text(str(_n[0]))
    run(wc, "git", "add", "-A")
    subprocess.run(["git", "commit", "-q", "-F", "-"], cwd=str(wc), input=msg, text=True,
                   check=True, capture_output=True)


def push(wc, *refspec):
    return run(wc, "git", "push", "-q", "origin", *(refspec or ("HEAD:main",)), check=False)


def refused(p):
    return p.returncode != 0 and "push REFUSED" in p.stderr


def test_lumen_pr_house_format_is_accepted(forge):
    commit(forge["wc"], "fix(wq): refuse done on edits that never landed\n\nbody\n\n"
           "Refs: wq#5132\n" + ASSIST + "\nCo-authored-by: Lumen Industries <lumen@lumennode.uk>")
    assert push(forge["wc"]).returncode == 0


def test_ai_tool_as_co_author_is_refused(forge):
    commit(forge["wc"], "fix(x): thing\n\nCo-authored-by: Claude <noreply@anthropic.com>")
    p = push(forge["wc"])
    assert refused(p), p.stderr


def test_undisclosed_ai_stamp_is_refused(forge):
    commit(forge["wc"], "fix(x): thing\n\nGenerated with [Claude Code](https://claude.com/claude-code)")
    assert refused(push(forge["wc"]))


def test_non_conventional_subject_is_refused(forge):
    commit(forge["wc"], "Updated some stuff\n\n" + ASSIST)
    assert refused(push(forge["wc"]))


def test_subject_over_80_is_refused(forge):
    commit(forge["wc"], "fix(x): " + "a" * 80 + "\n\n" + ASSIST)
    assert refused(push(forge["wc"]))


def test_wip_is_refused(forge):
    commit(forge["wc"], "WIP: half done")
    assert refused(push(forge["wc"]))


def test_one_bad_commit_in_a_series_refuses_the_whole_push(forge):
    commit(forge["wc"], "fix(x): good one\n\n" + ASSIST)
    commit(forge["wc"], "oops")
    commit(forge["wc"], "fix(x): good two\n\n" + ASSIST)
    p = push(forge["wc"])
    assert refused(p), p.stderr
    assert p.stderr.count("(policy):") == 1, p.stderr


def test_feature_branch_push_is_checked_too(forge):
    run(forge["wc"], "git", "checkout", "-q", "-b", "fix/thing")
    commit(forge["wc"], "nope not conventional")
    assert refused(push(forge["wc"], "HEAD:refs/heads/fix/thing"))


def test_missing_checker_fails_closed(forge):
    shutil.rmtree(forge["lib"] / "site")
    commit(forge["wc"], "fix(x): fine message\n\n" + ASSIST)
    p = push(forge["wc"])
    assert refused(p) and "not installed" in p.stderr


def test_broken_config_fails_closed(forge):
    (forge["etc"] / "cchk.toml").write_text("[commit\nthis is not toml")
    commit(forge["wc"], "fix(x): fine message\n\n" + ASSIST)
    p = push(forge["wc"])
    assert refused(p) and "checker error rc=2" in p.stderr, p.stderr


def test_break_glass_disabled_file_accepts(forge):
    (forge["etc"] / "DISABLED").write_text("")
    commit(forge["wc"], "not conventional at all")
    p = push(forge["wc"])
    assert p.returncode == 0 and "DISABLED" in p.stderr


def test_commits_already_on_the_server_are_not_rechecked(forge):
    (forge["etc"] / "DISABLED").write_text("")
    commit(forge["wc"], "legacy bad message")
    assert push(forge["wc"]).returncode == 0
    os.remove(forge["etc"] / "DISABLED")
    commit(forge["wc"], "fix(x): new good commit\n\n" + ASSIST)
    assert push(forge["wc"]).returncode == 0


def test_branch_delete_and_tag_push_are_skipped(forge):
    run(forge["wc"], "git", "tag", "-a", "v1", "-m", "whatever tag message")
    assert push(forge["wc"], "refs/tags/v1").returncode == 0
    run(forge["wc"], "git", "checkout", "-q", "-b", "fix/tmp")
    commit(forge["wc"], "fix(x): tmp\n\n" + ASSIST)
    assert push(forge["wc"], "HEAD:refs/heads/fix/tmp").returncode == 0
    assert push(forge["wc"], ":refs/heads/fix/tmp").returncode == 0


def test_exempt_repo_accepts_anything(forge):
    (forge["etc"] / "exempt-repos").write_text("# data syncs\nother/thing\n" +
                                              forge["bare"].parent.name + "/repo\n")
    commit(forge["wc"], "auto: fleet memory sync 2026-10-08T07:08:14Z")
    assert push(forge["wc"]).returncode == 0


def test_exempt_list_naming_another_repo_does_not_exempt_this_one(forge):
    (forge["etc"] / "exempt-repos").write_text("lumen/obsidian-vault\n")
    commit(forge["wc"], "auto: fleet memory sync 2026-10-08T07:08:14Z")
    assert refused(push(forge["wc"]))
