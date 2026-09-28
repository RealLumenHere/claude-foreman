"""wq#3035 (wq#2922 root cause): _check_citation proves a citation matches the worker's
own branch diff, never that it landed. citation_landed_on_main/citations_landed are the
done-time counterpart — checked directly here, isolated from fworker's larger machinery
(see test_evidence_gate.py for the end-to-end check_artifacts_done version)."""
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import acceptance  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    """main with a base file; a side branch that adds impl.py and never merges back."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(r)],
                   check=True)
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "test")
    (r / "base.txt").write_text("base\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "checkout", "-q", "-b", "side")
    (r / "impl.py").write_text("".join(f"line {i}\n" for i in range(1, 11)))
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "unlanded fix")
    _git(r, "checkout", "-q", "main")
    return r


def test_a_citation_only_on_the_unmerged_branch_is_rejected(repo):
    ok, why = acceptance.citation_landed_on_main({"citation": "impl.py:1-5"}, str(repo))
    assert not ok
    assert "unmerged branch" in why


def test_the_same_citation_is_accepted_once_the_branch_lands_on_main(repo):
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", "land it")
    ok, why = acceptance.citation_landed_on_main({"citation": "impl.py:1-5"}, str(repo))
    assert ok, why


def test_a_line_range_beyond_mains_copy_of_the_file_is_rejected(repo):
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", "land it")
    ok, why = acceptance.citation_landed_on_main({"citation": "impl.py:1-999"}, str(repo))
    assert not ok
    assert "cited 1-999" in why


def test_a_crossrepo_citation_is_not_rechecked_here(repo):
    """_crossrepo_citation already demanded its rev be an ancestor of the OTHER repo's
    main; this function must not re-litigate that against the LOCAL repo's main."""
    ok, why = acceptance.citation_landed_on_main(
        {"citation": "impl.py:1", "repo": "/some/other/repo", "rev": "deadbeef"}, str(repo))
    assert ok
    assert "pins and checks its own" in why


def test_citations_landed_reports_every_failing_id(repo):
    pinned = {"criteria": [
        {"id": "AC1", "proof_type": "test"},
        {"id": "AC2", "proof_type": "citation"},
        {"id": "AC3", "proof_type": "citation"},
    ]}
    evidence = {"criteria": [
        {"id": "AC2", "citation": "impl.py:1"},
        {"id": "AC3", "citation": "base.txt:1"},
    ]}
    ok, failed = acceptance.citations_landed(pinned, evidence, str(repo))
    assert not ok
    assert {f["id"] for f in failed} == {"AC2"}


def test_citations_landed_passes_once_everything_cited_is_on_main(repo):
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", "land it")
    pinned = {"criteria": [{"id": "AC1", "proof_type": "test"},
                           {"id": "AC2", "proof_type": "citation"}]}
    evidence = {"criteria": [{"id": "AC2", "citation": "impl.py:1"}]}
    ok, failed = acceptance.citations_landed(pinned, evidence, str(repo))
    assert (ok, failed) == (True, [])
