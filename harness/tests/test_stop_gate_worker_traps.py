"""stop_gate.py's two mechanical traps (wq#1915(A)) -- independent of, and layered
on top of, the pre-existing acceptance-evidence gate covered by test_evidence_gate.py.

(A) stop-with-work-on-the-floor: wq#1888 (238 turns, 11 modified + 3 new files) and
    wq#1905 (157 turns, 8 files) both ended their turn with "I will wait for the
    background test task" -- claude -p returned result:success, nothing was
    committed, evidence replay saw an empty branch diff and the ticket went
    FALSE_COMPLETE.
(B) a run_in_background command with no recorded completion in the transcript.

Both trip only when the ticket is not already done, and both share the acceptance
gate's MAX_BLOCKS counter (a confused worker gets 3 total nudges, not 3 per reason).
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "stop_gate", os.path.join(HARNESS_DIR, "hooks", "stop_gate.py"))
stop_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stop_gate)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, check=True)


@pytest.fixture
def landed_repo(tmp_path):
    """A repo whose HEAD is already 'origin/main' -- i.e. the branch has landed
    nothing of its own yet. This is the state a freshly `lumen-pr start --worktree`ed
    branch is in before the worker does anything."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "base.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _transcript(tmp_path, records):
    p = tmp_path / "transcript.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


def _assistant_text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _bg_launch(tool_id="toolu_1"):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Bash",
         "input": {"command": "npm test", "run_in_background": True}}]}}


def _bg_poll_completed():
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": '{"status": "completed", "shell_id": "s1"}'}]}}


# ---------------------------------------------------------------------------
# (A) uncommitted work on the floor
# ---------------------------------------------------------------------------

def test_dirty_worktree_never_landed_blocks(landed_repo, tmp_path, monkeypatch):
    (landed_repo / "wip.py").write_text("still editing\n")  # untracked, uncommitted
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "stop-gate.json"))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    code, msg = stop_gate.decide({"session_id": "s1"})
    assert code == stop_gate.BLOCK
    assert "uncommitted work on the floor" in msg
    assert "lumen-pr commit" in msg


def test_clean_worktree_never_blocks(landed_repo, monkeypatch):
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    assert stop_gate.decide({"session_id": "s1"}) == (stop_gate.ALLOW, "")


def test_dirty_but_already_landed_a_commit_does_not_block(landed_repo, monkeypatch):
    """origin/main..HEAD non-empty (a commit already landed on this branch) means the
    (A) trap's own AND condition is false, even though the tree is currently dirty
    again on top of that commit -- matches the ticket's literal 'no lumen-pr commit
    has happened' condition, not 'the tree is clean'."""
    (landed_repo / "impl.py").write_text("done\n")
    _git(landed_repo, "add", "-A")
    _git(landed_repo, "commit", "-q", "-m", "work")
    (landed_repo / "more.py").write_text("still going\n")
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    assert stop_gate.decide({"session_id": "s1"}) == (stop_gate.ALLOW, "")


def test_ticket_closed_sentinel_suppresses_the_dirty_trap(landed_repo, tmp_path, monkeypatch):
    (landed_repo / "wip.py").write_text("still editing\n")
    transcript = _transcript(tmp_path, [
        _assistant_text("Nothing to change here after all.\nTICKET_CLOSED")])
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    assert stop_gate.decide(
        {"session_id": "s1", "transcript_path": transcript}) == (stop_gate.ALLOW, "")


def test_blocked_sentinel_also_suppresses_the_dirty_trap(landed_repo, tmp_path, monkeypatch):
    (landed_repo / "wip.py").write_text("partial\n")
    transcript = _transcript(tmp_path, [_assistant_text("Can't proceed.\nBLOCKED")])
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    assert stop_gate.decide(
        {"session_id": "s1", "transcript_path": transcript}) == (stop_gate.ALLOW, "")


def test_a_sentinel_look_alike_mid_message_does_not_count(landed_repo, tmp_path, monkeypatch):
    """Only the LAST non-empty line counts (classifier.parse_worker_sentinel's own
    contract) -- a worker that merely mentions TICKET_CLOSED in passing must not dodge
    the trap."""
    (landed_repo / "wip.py").write_text("partial\n")
    transcript = _transcript(tmp_path, [
        _assistant_text("I saw a TICKET_CLOSED sentinel mentioned in the doc, "
                        "but I'm not done.\nI'll keep going next turn.")])
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "stop-gate.json"))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    code, _ = stop_gate.decide({"session_id": "s1", "transcript_path": transcript})
    assert code == stop_gate.BLOCK


def test_ticket_already_done_never_blocks_even_when_dirty(landed_repo, monkeypatch):
    (landed_repo / "wip.py").write_text("still editing\n")
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: True)
    assert stop_gate.decide({"session_id": "s1"}) == (stop_gate.ALLOW, "")


# ---------------------------------------------------------------------------
# (B) pending background task
# ---------------------------------------------------------------------------

def test_unpolled_background_task_blocks(landed_repo, tmp_path, monkeypatch):
    transcript = _transcript(tmp_path, [_bg_launch()])
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "stop-gate.json"))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    code, msg = stop_gate.decide({"session_id": "s1", "transcript_path": transcript})
    assert code == stop_gate.BLOCK
    assert "run_in_background" in msg and "BashOutput" in msg


def test_polled_background_task_does_not_block(landed_repo, tmp_path, monkeypatch):
    transcript = _transcript(tmp_path, [_bg_launch(), _bg_poll_completed()])
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    assert stop_gate.decide(
        {"session_id": "s1", "transcript_path": transcript}) == (stop_gate.ALLOW, "")


def test_no_transcript_path_never_blocks_on_background_task(landed_repo, monkeypatch):
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    assert stop_gate.decide({"session_id": "s1"}) == (stop_gate.ALLOW, "")


# ---------------------------------------------------------------------------
# shared cap + real-subprocess contract
# ---------------------------------------------------------------------------

def test_mechanical_traps_share_the_acceptance_gates_block_cap(landed_repo, tmp_path, monkeypatch):
    (landed_repo / "wip.py").write_text("still editing\n")
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(landed_repo))
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "stop-gate.json"))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    codes = [stop_gate.decide({"session_id": "s1"})[0] for _ in range(5)]
    assert codes == [stop_gate.BLOCK] * stop_gate.MAX_BLOCKS + [stop_gate.ALLOW] * 2


def test_stop_hook_honours_the_hook_contract_for_the_dirty_trap_as_a_real_subprocess(
        landed_repo, tmp_path):
    (landed_repo / "wip.py").write_text("still editing\n")
    env = dict(os.environ, FOREMAN_WORKTREE_ROOT=str(landed_repo),
               FOREMAN_STOP_GATE_STATE=str(tmp_path / "stop-gate.json"),
               FOREMAN_WQ_BIN="/bin/false", FOREMAN_TICKET_ID="9999")
    p = subprocess.run(
        [sys.executable, os.path.join(HARNESS_DIR, "hooks", "stop_gate.py")],
        input=json.dumps({"session_id": "s1", "cwd": str(landed_repo)}),
        capture_output=True, text=True, env=env)
    assert p.returncode == stop_gate.BLOCK
    assert "uncommitted work on the floor" in p.stderr
