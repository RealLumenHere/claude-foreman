"""wq#4762 (DESIGN.md §2.5): derive_verify_needs() must emit needs:repo(<key>@<sha>)
for the shipped ticket's own repo + merge commit, so the filed verify ticket can read
the EXACT shipped diff through the repo-read broker instead of the mutable primary-
tree add_dir. Covers: the happy path, an unmanaged shipped repo (no tag), a shipped
PR not yet found on the branch (no tag), and _shipped_merge_sha's real git grep.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import judgment  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True)


@pytest.fixture
def fixture_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "feat: thing\n\nRefs: wq#4321")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return str(repo), sha


def test_shipped_merge_sha_finds_refs_footer(fixture_repo):
    repo, sha = fixture_repo
    assert judgment._shipped_merge_sha(repo, 4321) == sha


def test_shipped_merge_sha_none_for_unknown_ticket(fixture_repo):
    repo, _sha = fixture_repo
    assert judgment._shipped_merge_sha(repo, 9999) is None


def test_derive_adds_repo_tag_for_managed_shipped_repo(monkeypatch, fixture_repo):
    repo, sha = fixture_repo
    monkeypatch.setattr(judgment.router, "repo_for_ticket", lambda t: repo)
    monkeypatch.setattr(judgment.repo_sweep, "managed_repos", lambda: {repo})
    t = {"id": 4321, "detail": "", "notes": ""}
    derived, block_reasons = judgment.derive_verify_needs(t)
    assert derived.repo_grants == [(repo, sha)]
    assert block_reasons == []
    # the repo() tag alone must not push this ticket into verify-probe (§5.2's
    # trigger set is ssh/homeroot/minerva/ops_verified -- "repo" is not in it).
    archetype, _reason = judgment.router.classify_verify_archetype(derived)
    assert archetype == "verify-pass"


def test_derive_skips_repo_tag_for_unmanaged_shipped_repo(monkeypatch, fixture_repo):
    repo, _sha = fixture_repo
    monkeypatch.setattr(judgment.router, "repo_for_ticket", lambda t: repo)
    monkeypatch.setattr(judgment.repo_sweep, "managed_repos", lambda: {"/some/other/repo"})
    t = {"id": 4321, "detail": "", "notes": ""}
    derived, _block_reasons = judgment.derive_verify_needs(t)
    assert derived.repo_grants == []


def test_derive_skips_repo_tag_when_merge_sha_not_found(monkeypatch, fixture_repo):
    repo, _sha = fixture_repo
    monkeypatch.setattr(judgment.router, "repo_for_ticket", lambda t: repo)
    monkeypatch.setattr(judgment.repo_sweep, "managed_repos", lambda: {repo})
    t = {"id": 99999999, "detail": "", "notes": ""}  # no commit carries this Refs: footer
    derived, _block_reasons = judgment.derive_verify_needs(t)
    assert derived.repo_grants == []
