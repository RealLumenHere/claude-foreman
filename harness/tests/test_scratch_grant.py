"""wq#1697: the scratch dir wq tells workers to use, and the worktree they are given, are
grants the worker holds -- and a P0/P1 capability denial / unresolved read root becomes
one keyed, diff-carrying alert-queue row.
"""
import os
import stat
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import capability_proposal as cp  # noqa: E402
import failure_record as fr  # noqa: E402
import fworker  # noqa: E402
import scratch_grant as sg  # noqa: E402

PATH_BLOCK = ("was blocked. For security, Claude Code may only write files in "
              "the allowed working directories")
LIVE_WQ = sg.WQ_BIN_PATH


def _refused_write(path, uid="t0"):
    return [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": path}, "id": uid}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": uid, "is_error": True,
             "content": PATH_BLOCK}]}},
    ]


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo


# ---- fix 1: implicit grants ---------------------------------------------------------

def test_scratch_dir_matches_wq_formula():
    assert sg.scratch_dir("fworker/wq1", root="/tmp") == "/tmp/fworker_wq1"


def test_implicit_grants_are_worktree_plus_scratch():
    assert sg.implicit_grants("/wt", "agent-a", root="/tmp") == ["/wt", "/tmp/agent-a"]


def test_build_env_creates_scratch_0700_and_exports_it(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    monkeypatch.setattr(fworker, "read_oauth_token", lambda: "tok")
    env = fworker.build_env("w1", "sonnet-5", "high", str(repo), 4242)
    scratch = env["FOREMAN_SCRATCH_DIR"]
    assert scratch == sg.scratch_dir(env["WQ_AGENT"])
    assert stat.S_IMODE(os.stat(scratch).st_mode) == 0o700


def test_scratch_dir_is_an_add_dir_for_the_run():
    assert fworker.scratch_grant_dirs(4242) == [sg.scratch_dir(fworker.wq_identity(4242))]


def test_sandbox_ticket_writing_scratch_db_and_worktree_file_is_not_denied():
    """Test-to-failure: the exact wq#1610/#1616 shape. A write the CLI refused under a
    reach of {worktree} alone is a dir() need; under the run's real reach it is not."""
    worktree = "/home/lumen/worktrees/wq4242-a1"
    scratch = sg.scratch_dir(fworker.wq_identity(4242))
    events = (_refused_write(f"{scratch}/x.db", "a") +
              _refused_write(f"{worktree}/harness/f.py", "b"))
    _, before, _ = fr.extract_denied_calls(events, reachable_roots=[worktree])
    assert f"{scratch}/x.db" in before
    reach = [worktree] + fworker.scratch_grant_dirs(4242)
    _, after, _ = fr.extract_denied_calls(events, reachable_roots=reach)
    assert after == []


def test_bare_tmp_is_still_not_granted():
    reach = ["/wt"] + fworker.scratch_grant_dirs(4242)
    _, unreachable, _ = fr.extract_denied_calls(
        _refused_write("/tmp/wq1610_cli_test.db"), reachable_roots=reach)
    assert unreachable == ["/tmp/wq1610_cli_test.db"]


@pytest.mark.skipif(not os.path.exists(LIVE_WQ), reason="bin/wq not on this host")
def test_policy_lint_agrees_with_the_live_wq_scratch_rule():
    assert sg.lint_agreement(root="/tmp") == []


def test_policy_lint_fails_when_wq_and_harness_disagree(tmp_path):
    wq = tmp_path / "wq"
    wq.write_text('SCRATCH_ROOT = os.environ.get("WQ_SCRATCH_ROOT", "/var/scratch")\n')
    assert any("/var/scratch" in p for p in sg.lint_agreement(str(wq), root="/tmp"))


def test_policy_lint_fails_when_wq_rule_is_unreadable(tmp_path):
    assert sg.lint_agreement(str(tmp_path / "missing"), root="/tmp")


def test_policy_lint_fails_when_scratch_root_is_denied(tmp_path):
    wq = tmp_path / "wq"
    wq.write_text('SCRATCH_ROOT = os.environ.get("WQ_SCRATCH_ROOT", "/home/lumen/.secrets")\n')
    problems = sg.lint_agreement(str(wq), root="/home/lumen/.secrets")
    assert any("deny-list" in p for p in problems)


# ---- fix 2: unresolved read roots -> one keyed proposal ------------------------------

def test_only_unresolved_read_dirs_are_proposable():
    lines = ["dir(/home/lumen/infra) is not under allow.read_dirs — propose by PR",
             "cited path /home/lumen/x does not exist (not guessed)",
             "'ssh' is a v2 verb: no fleet-exec grant exists for it"]
    assert cp.unresolved_dir_paths(lines) == ["/home/lumen/infra"]


def test_grant_key_is_stable_per_path_not_per_ticket():
    assert cp.grant_key("/a") == cp.grant_key("/a") != cp.grant_key("/b")


def test_policy_diff_is_an_applicable_read_dirs_addition():
    d = cp.policy_diff(["/home/lumen/infra"], 1672)
    assert "+    - /home/lumen/infra  # wq#1672" in d
    assert "harness/fleet-exec-policy.yaml" in d


def _fake_aq(tmp_path):
    log = tmp_path / "aq.log"
    aq = tmp_path / "aq"
    aq.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> {log}\n')
    aq.chmod(0o755)
    return str(aq), log


def test_file_dir_grant_calls_aq_add_with_the_path_key(tmp_path):
    aq, log = _fake_aq(tmp_path)
    assert cp.file_dir_grant("/home/lumen/infra", 1672, aq_bin=aq)
    row = log.read_text()
    assert row.startswith(f"add {cp.AQ_SOURCE} {cp.grant_key('/home/lumen/infra')} ")
    assert "+    - /home/lumen/infra" in row


def test_missing_aq_binary_fails_open(tmp_path):
    assert cp.file_dir_grant("/x", 1, aq_bin=str(tmp_path / "nope")) is False


# ---- fix 3: P0/P1 CAPABILITY_DENIED -> aq row with the exact diff ---------------------

def _denied_record():
    return {"triage": "CAPABILITY_DENIED", "summary": "dir(/home/lumen/infra), "
            "dir(/home/lumen/worktrees/other-a1) refused 13x",
            "inferred_needs": ["dir(/home/lumen/infra)",
                               "dir(/home/lumen/worktrees/other-a1)", "ssh(thor)"]}


def test_body_splits_grantable_forbidden_and_non_dir_needs():
    body = cp.denied_row_body(1672, _denied_record(), "/x/failure.json")
    assert "+    - /home/lumen/infra  # wq#1672" in body
    assert "NOT grantable" in body and "/home/lumen/worktrees/other-a1" in body
    assert "ssh(thor)" in body and "/x/failure.json" in body
    grantable_part = body.split("NOT grantable")[0]
    assert "worktrees/other-a1" not in grantable_part.split("Apply")[1]


@pytest.mark.parametrize("pri,filed", [(0, True), (1, True), (2, False), (None, False)])
def test_only_p0_p1_capability_denials_open_a_row(tmp_path, pri, filed):
    aq, log = _fake_aq(tmp_path)
    got = cp.file_denied_row({"id": 1672, "pri": pri}, _denied_record(), aq_bin=aq)
    assert got is filed
    assert log.exists() is filed


def test_other_triage_never_opens_a_row(tmp_path):
    aq, log = _fake_aq(tmp_path)
    rec = dict(_denied_record(), triage="GENUINE_BUG")
    assert cp.file_denied_row({"id": 1, "pri": 0}, rec, aq_bin=aq) is False
    assert not log.exists()
