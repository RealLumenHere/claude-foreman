"""Unit tests for make_worktree()'s branch/worktree-path naming logic (wq#814).

Pure string-derivation only: subprocess/git calls are stubbed so this never touches a
real repo. See fworker.make_worktree() for the naming rules under test.
"""
import os
import sys
from unittest import mock

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402


def _ok(stdout=""):
    return mock.Mock(returncode=0, stdout=stdout, stderr="")


def _make(ticket_id, attempt, title, archetype):
    with mock.patch.object(fworker, "_git", return_value=_ok()) as git, \
         mock.patch("os.makedirs"), \
         mock.patch("os.path.isdir", return_value=False):
        wt, branch = fworker.make_worktree("/repo", ticket_id, attempt, title, archetype)
    return wt, branch, git


def test_distinct_ticket_ids_produce_distinct_names():
    wt1, branch1, _ = _make(100, 1, "fix the thing", "mechanical-edit")
    wt2, branch2, _ = _make(200, 1, "fix the thing", "mechanical-edit")

    assert wt1 != wt2
    assert branch1 != branch2
    assert branch1 == "chore/wq100-fix-the-thing-a1"
    assert branch2 == "chore/wq200-fix-the-thing-a1"
    assert wt1.endswith("wq100-a1")
    assert wt2.endswith("wq200-a1")


def test_distinct_attempt_numbers_produce_distinct_names():
    wt1, branch1, _ = _make(814, 1, "probe build", "probe-build")
    wt2, branch2, _ = _make(814, 2, "probe build", "probe-build")

    assert wt1 != wt2
    assert branch1 != branch2
    assert branch1 == "test/wq814-probe-build-a1"
    assert branch2 == "test/wq814-probe-build-a2"
    assert wt1.endswith("wq814-a1")
    assert wt2.endswith("wq814-a2")


def test_archetype_maps_to_expected_branch_type_prefix():
    _, branch, _ = _make(1, 1, "novel idea", "novel-design")
    assert branch.startswith("docs/wq1-")

    _, branch, _ = _make(1, 1, "root cause fix", "rca-investigation")
    assert branch.startswith("fix/wq1-")

    _, branch, _ = _make(1, 1, "unknown archetype", "some-unmapped-archetype")
    assert branch.startswith("feat/wq1-")


def test_branch_and_worktree_path_are_valid_git_ref_safe_strings():
    wt, branch, _ = _make(42, 3, "Fix Weird/Title!! With Spaces", "mechanical-edit")

    # git refs must not contain spaces, "..", or most punctuation; slugify() must
    # have stripped it all out.
    assert " " not in branch
    assert ".." not in branch
    assert branch == branch.lower()
    assert branch.endswith("-a3")
    assert os.path.basename(wt) == "wq42-a3"


def test_calls_git_worktree_add_with_derived_branch_and_path():
    wt, branch, git = _make(9, 1, "some title", "mechanical-edit")

    add_calls = [c for c in git.call_args_list if "add" in c.args]
    assert add_calls, "expected a `git worktree add` call"
    call = add_calls[0]
    assert wt in call.args
    assert "-b" in call.args
    assert branch in call.args
