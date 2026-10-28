"""wq#4765 (agent-authority DESIGN.md §2.3 steps 2-6, §2.6, §2.7 F4/F7/F9): fworker's
hold/resume support — the tool_deferred cross-check, EXIT_AWAITING_GRANT/
EXIT_HOLD_RESUME_LOST, and the grant-approval resume's bare `-p --resume` argv.

Same real-`wq`-against-a-scratch-`WQ_DB` pattern as test_fworker_wq_show.py /
test_fworker_grant.py for the grant_requests reads; build_claude_cmd's resume_no_prompt
shape is a pure function, no wq needed.
"""
import hashlib
import os
import re
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402
from classifier import RunState  # noqa: E402

WQ_BIN = "/home/lumen/bin/wq"


def fp(seed):
    return hashlib.sha256(seed.encode()).hexdigest()


@pytest.fixture
def wq_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WQ_DB", str(tmp_path / "queue.db"))
    monkeypatch.delenv("WQ_AGENT", raising=False)
    return tmp_path


def add_ticket(title="wq4765 fixture"):
    p = subprocess.run([WQ_BIN, "add", "--lane", "build", "--title", title],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(re.search(r"added task (\d+)", p.stdout).group(1))


def claim(tid, agent):
    p = subprocess.run([WQ_BIN, "claim", "--id", str(tid)],
                       capture_output=True, text=True, env={**os.environ, "WQ_AGENT": agent})
    assert p.returncode == 0, p.stdout + p.stderr


def file_request(tid, agent, seed, session, tool_use="toolu_01abc", klass="H1",
                  target="/srv/granted/dir"):
    p = subprocess.run(
        [WQ_BIN, "grant", "request", str(tid), "--fingerprint", fp(seed),
         "--session", session, "--tool-use", tool_use, "--class", klass, "--target", target],
        capture_output=True, text=True, env={**os.environ, "WQ_AGENT": agent})
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.split("id=")[1].split()[0])


# --------------------------------------------------------------------------- grant reads

def test_grant_pending_row_matches_ticket_and_session(wq_env):
    tid = add_ticket()
    agent = f"fworker-wq{tid}"
    claim(tid, agent)
    rid = file_request(tid, agent, "a1", session="sess-a1")
    row = fworker.grant_pending_row(tid, "sess-a1")
    assert row is not None
    assert row["id"] == rid
    assert row["tool_use_id"] == "toolu_01abc"
    assert row["class"] == "H1"
    assert row["target"] == "/srv/granted/dir"


def test_grant_pending_row_wrong_session_returns_none(wq_env):
    tid = add_ticket()
    agent = f"fworker-wq{tid}"
    claim(tid, agent)
    file_request(tid, agent, "a2", session="sess-real")
    assert fworker.grant_pending_row(tid, "sess-other") is None


def test_grant_pending_row_no_session_returns_none(wq_env):
    assert fworker.grant_pending_row(123, None) is None
    assert fworker.grant_pending_row(123, "") is None


def test_grant_row_by_id_reads_class_and_target(wq_env):
    tid = add_ticket()
    agent = f"fworker-wq{tid}"
    claim(tid, agent)
    rid = file_request(tid, agent, "a3", session="sess-a3", klass="H1", target="/x/y")
    row = fworker.grant_row_by_id(rid)
    assert row["class"] == "H1" and row["target"] == "/x/y"


def test_grant_row_by_id_missing_returns_none(wq_env):
    assert fworker.grant_row_by_id(999999) is None


# --------------------------------------------------------------------- handle_tool_deferred

def test_handle_tool_deferred_match_parks_ticket_and_returns_exit_code(wq_env, monkeypatch):
    calls = {"ntfy": []}
    monkeypatch.setattr(fworker, "ntfy",
                       lambda title, msg, priority="default": calls["ntfy"].append((title, msg)))
    tid = add_ticket()
    agent = f"fworker-wq{tid}"
    claim(tid, agent)
    file_request(tid, agent, "match", session="sess-match", tool_use="toolu_match")

    st = RunState()
    st.session_id = "sess-match"
    st.deferred_tool_use = {"id": "toolu_match", "name": "Read"}

    monkeypatch.setenv("WQ_AGENT", agent)
    outcome = fworker.handle_tool_deferred(tid, st)
    assert outcome == fworker.EXIT_AWAITING_GRANT
    assert calls["ntfy"], "must notify with approve/deny commands"
    title, msg = calls["ntfy"][0]
    assert "approve" in msg and "deny" in msg

    row = fworker.wq_show(tid)
    assert row["status"] == "awaiting_grant"
    assert row["claimed_by"] in (None, "")


def test_handle_tool_deferred_mismatch_returns_none_and_leaves_ticket_claimed(wq_env, monkeypatch):
    calls = {"ntfy": []}
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: calls["ntfy"].append(a))
    tid = add_ticket()
    agent = f"fworker-wq{tid}"
    claim(tid, agent)
    file_request(tid, agent, "mismatch", session="sess-mismatch", tool_use="toolu_real")

    st = RunState()
    st.session_id = "sess-mismatch"
    # a DIFFERENT tool_use_id than what hold_gate actually filed — §2.6's HOLD_FRAME_MISMATCH.
    st.deferred_tool_use = {"id": "toolu_NOT_THE_SAME", "name": "Read"}

    monkeypatch.setenv("WQ_AGENT", agent)
    outcome = fworker.handle_tool_deferred(tid, st)
    assert outcome is None
    assert calls["ntfy"] == [], "a mismatch must never park the ticket or notify"

    row = fworker.wq_show(tid)
    assert row["status"] == "claimed"
    assert row["claimed_by"] == agent


def test_handle_tool_deferred_no_pending_row_returns_none(wq_env, monkeypatch):
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: None)
    tid = add_ticket()
    agent = f"fworker-wq{tid}"
    claim(tid, agent)
    # no `grant request` was ever filed for this session — e.g. hold_gate crashed.
    st = RunState()
    st.session_id = "sess-none"
    st.deferred_tool_use = {"id": "toolu_x"}
    monkeypatch.setenv("WQ_AGENT", agent)
    assert fworker.handle_tool_deferred(tid, st) is None


# ------------------------------------------------------------------- build_claude_cmd shape

def test_build_claude_cmd_ordinary_call_is_unaffected():
    cmd = fworker.build_claude_cmd("do the thing", "claude-sonnet-5", "high",
                                   "/settings.json", add_dirs=[])
    assert cmd[:3] == ["claude", "-p", "do the thing"]
    assert "--resume" not in cmd


def test_build_claude_cmd_resume_no_prompt_has_bare_dash_p_and_resume_flag():
    cmd = fworker.build_claude_cmd(None, "claude-sonnet-5", "high", "/settings.json",
                                   add_dirs=[], resume_session="sess-123",
                                   resume_no_prompt=True)
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    # F9: no prompt text rides along with -p — the very next token is a flag, not text.
    assert cmd[2].startswith("--")
    assert "--resume" in cmd and "sess-123" in cmd


def test_build_claude_cmd_resume_no_prompt_refuses_a_real_prompt():
    with pytest.raises(ValueError, match="refuses to spawn a resume with a prompt"):
        fworker.build_claude_cmd("a real prompt", "claude-sonnet-5", "high",
                                 "/settings.json", add_dirs=[], resume_session="sess-123",
                                 resume_no_prompt=True)


def test_build_claude_cmd_resume_no_prompt_requires_a_session():
    with pytest.raises(ValueError, match="requires resume_session"):
        fworker.build_claude_cmd(None, "claude-sonnet-5", "high", "/settings.json",
                                 add_dirs=[], resume_no_prompt=True)


def test_build_claude_cmd_resume_no_prompt_still_carries_add_dirs():
    cmd = fworker.build_claude_cmd(None, "claude-sonnet-5", "high", "/settings.json",
                                   add_dirs=["/srv/granted/dir"], resume_session="sess-123",
                                   resume_no_prompt=True)
    assert "--add-dir" in cmd
    assert cmd[cmd.index("--add-dir") + 1] == "/srv/granted/dir"
