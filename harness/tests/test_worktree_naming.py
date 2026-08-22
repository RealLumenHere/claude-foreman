"""Unit tests for make_worktree() branch/worktree-path naming logic.
Tests pure string derivation only — git subprocess calls are mocked to avoid
touching a real repository.
"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fworker import make_worktree


@patch("fworker._git")
@patch("fworker.os.makedirs")
@patch("fworker.os.path.isdir")
@patch("fworker.log")
def test_worktree_naming_ticket_123_attempt_1(
    mock_log, mock_isdir, mock_makedirs, mock_git
):
    """Test basic naming for ticket 123, attempt 1 with probe-build archetype."""
    mock_isdir.return_value = False
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_git.return_value = mock_result

    wt, branch = make_worktree(
        repo="/tmp/testrepo",
        ticket_id=123,
        attempt=1,
        title="test-feature",
        archetype="probe-build",
    )

    # probe-build archetype maps to "test" branch type
    expected_branch = "test/wq123-test-feature-a1"
    expected_wt = f"/home/lumen/worktrees/wq123-a1"

    assert branch == expected_branch, f"Expected {expected_branch}, got {branch}"
    assert wt == expected_wt, f"Expected {expected_wt}, got {wt}"


@patch("fworker._git")
@patch("fworker.os.makedirs")
@patch("fworker.os.path.isdir")
@patch("fworker.log")
def test_worktree_naming_ticket_456_attempt_2(
    mock_log, mock_isdir, mock_makedirs, mock_git
):
    """Test naming for a different ticket (456) with attempt 2 and mechanical-edit archetype."""
    mock_isdir.return_value = False
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_git.return_value = mock_result

    wt, branch = make_worktree(
        repo="/tmp/testrepo",
        ticket_id=456,
        attempt=2,
        title="fix-bug-in-parser",
        archetype="mechanical-edit",
    )

    # mechanical-edit archetype maps to "chore" branch type
    expected_branch = "chore/wq456-fix-bug-in-parser-a2"
    expected_wt = f"/home/lumen/worktrees/wq456-a2"

    assert branch == expected_branch, f"Expected {expected_branch}, got {branch}"
    assert wt == expected_wt, f"Expected {expected_wt}, got {wt}"


@patch("fworker._git")
@patch("fworker.os.makedirs")
@patch("fworker.os.path.isdir")
@patch("fworker.log")
def test_worktree_naming_rca_investigation(
    mock_log, mock_isdir, mock_makedirs, mock_git
):
    """Test naming with rca-investigation archetype (maps to 'fix')."""
    mock_isdir.return_value = False
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_git.return_value = mock_result

    wt, branch = make_worktree(
        repo="/tmp/testrepo",
        ticket_id=789,
        attempt=1,
        title="crash-in-memory-handler",
        archetype="rca-investigation",
    )

    # rca-investigation archetype maps to "fix" branch type
    expected_branch = "fix/wq789-crash-in-memory-handler-a1"
    expected_wt = f"/home/lumen/worktrees/wq789-a1"

    assert branch == expected_branch, f"Expected {expected_branch}, got {branch}"
    assert wt == expected_wt, f"Expected {expected_wt}, got {wt}"


@patch("fworker._git")
@patch("fworker.os.makedirs")
@patch("fworker.os.path.isdir")
@patch("fworker.log")
def test_worktree_naming_default_archetype(
    mock_log, mock_isdir, mock_makedirs, mock_git
):
    """Test naming with an unknown archetype (defaults to 'feat')."""
    mock_isdir.return_value = False
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_git.return_value = mock_result

    wt, branch = make_worktree(
        repo="/tmp/testrepo",
        ticket_id=999,
        attempt=1,
        title="new-feature",
        archetype="unknown-archetype",
    )

    # unknown archetype defaults to "feat" branch type
    expected_branch = "feat/wq999-new-feature-a1"
    expected_wt = f"/home/lumen/worktrees/wq999-a1"

    assert branch == expected_branch, f"Expected {expected_branch}, got {branch}"
    assert wt == expected_wt, f"Expected {expected_wt}, got {wt}"


@patch("fworker._git")
@patch("fworker.os.makedirs")
@patch("fworker.os.path.isdir")
@patch("fworker.log")
def test_worktree_naming_title_slugification(
    mock_log, mock_isdir, mock_makedirs, mock_git
):
    """Test that titles are slugified correctly in branch names."""
    mock_isdir.return_value = False
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_git.return_value = mock_result

    wt, branch = make_worktree(
        repo="/tmp/testrepo",
        ticket_id=111,
        attempt=1,
        title="Fix URGENT: Multiple   Spaces & Special!@# Chars",
        archetype="probe-build",
    )

    # title should be slugified: lowercased, spaces replaced with -, special chars removed
    expected_branch = "test/wq111-fix-urgent-multiple-spaces-special-chars-a1"
    expected_wt = f"/home/lumen/worktrees/wq111-a1"

    assert branch == expected_branch, f"Expected {expected_branch}, got {branch}"
    assert wt == expected_wt, f"Expected {expected_wt}, got {wt}"


@patch("fworker._git")
@patch("fworker.os.makedirs")
@patch("fworker.os.path.isdir")
@patch("fworker.log")
def test_worktree_prune_called(
    mock_log, mock_isdir, mock_makedirs, mock_git
):
    """Test that git worktree prune is called before attempting add."""
    mock_isdir.return_value = False
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_git.return_value = mock_result

    make_worktree(
        repo="/tmp/testrepo",
        ticket_id=222,
        attempt=1,
        title="test",
        archetype="probe-build",
    )

    # First call should be "git -C /tmp/testrepo worktree prune"
    mock_git.assert_any_call("/tmp/testrepo", "worktree", "prune")
