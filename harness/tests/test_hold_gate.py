"""hold_gate.py tests — DESIGN.md §2.2/§2.3/§2.6/§2.7 (wq#4760 B1-T1).

Zero quota. Feeds the PreToolUse hook envelope on stdin via a subprocess (same
harness contract test_guard.py uses) and asserts the decision JSON; fingerprint
and approval-consume invariants are tested in-process. Ticket-mandated cases:
guard-denied call → deny not defer; credential path never held; Write never
held; consumed approval → deny; fingerprint differs on different tool_use_id.
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hold_gate  # noqa: E402

HOLD_GATE = os.path.join(os.path.dirname(__file__), "..", "hold_gate.py")
WORKTREE = "/home/lumen/worktrees/wq999-a1"
# An existing, credential-free, non-symlink file outside any add_dir: the
# canonical H1 target (not /etc/os-release — a symlink, so the stored realpath
# target would differ from the cited path and the row assertions get noisy).
OUTSIDE_FILE = "/etc/hostname"


@pytest.fixture
def hold_env(tmp_path):
    env = dict(os.environ)
    env.update({
        "FOREMAN_WORKER": "test-worker",
        "FOREMAN_TICKET_ID": "4760",
        "FOREMAN_WORKTREE_ROOT": WORKTREE,
        "FOREMAN_ADD_DIRS": f"{WORKTREE}:/tmp/fworker-wq4760",
        "FOREMAN_HOLD_DB": str(tmp_path / "hold.db"),
        "FOREMAN_EVENTS_DIR": str(tmp_path / "events"),
    })
    env.pop("FOREMAN_HOLD_GATE", None)
    env.pop("FOREMAN_GRANT_FILE", None)
    return env


def run_hook(tool_name, tool_input, env, tool_use_id="toolu_01", session_id="sess-1",
             cwd=WORKTREE):
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "cwd": cwd,
    })
    p = subprocess.run([sys.executable, HOLD_GATE], input=payload,
                       capture_output=True, text=True, env=env, timeout=60)
    return p


def decision_of(proc):
    """permissionDecision from the hook stdout, or None for 'no decision'."""
    if not proc.stdout.strip():
        return None
    out = json.loads(proc.stdout)
    return out["hookSpecificOutput"]["permissionDecision"]


def db_rows(env):
    if not os.path.exists(env["FOREMAN_HOLD_DB"]):
        return []  # a decision made before the approvals step never opens the db
    conn = sqlite3.connect(env["FOREMAN_HOLD_DB"])
    try:
        return conn.execute(
            "SELECT id, state, class, target, fingerprint FROM grant_requests").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def set_state(env, fingerprint, state):
    conn = sqlite3.connect(env["FOREMAN_HOLD_DB"])
    try:
        conn.execute("UPDATE grant_requests SET state=? WHERE fingerprint=?",
                     (state, fingerprint))
        conn.commit()
    finally:
        conn.close()


# ---- holdable: H1 defer + full approve/consume lifecycle ----------------------

def test_outside_read_defers_and_writes_pending_row(hold_env):
    p = run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    assert p.returncode == 0, p.stderr
    assert decision_of(p) == "defer"
    rows = db_rows(hold_env)
    assert len(rows) == 1
    _id, state, cls, target, _fp = rows[0]
    assert (state, cls, target) == ("pending", "H1", OUTSIDE_FILE)
    # F2 text: tell the model to re-issue the held call alone.
    reason = json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "re-issue it alone" in reason


def test_pending_request_defers_again(hold_env):
    run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    p = run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    assert decision_of(p) == "defer"
    assert len(db_rows(hold_env)) == 1  # same fingerprint, no duplicate row


def test_approved_request_allows_once_then_denies(hold_env):
    run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    fp = db_rows(hold_env)[0][4]
    set_state(hold_env, fp, "approved")
    p = run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    assert decision_of(p) == "allow"
    assert db_rows(hold_env)[0][1] == "consumed"
    # ticket spec + F5: a consumed approval is a deny, never a re-defer.
    p2 = run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    assert decision_of(p2) == "deny"


def test_denied_request_denies(hold_env):
    run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    set_state(hold_env, db_rows(hold_env)[0][4], "denied")
    p = run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    assert decision_of(p) == "deny"


def test_bash_read_verb_outside_defers(hold_env):
    p = run_hook("Bash", {"command": f"cat {OUTSIDE_FILE}"}, hold_env)
    assert decision_of(p) == "defer", p.stderr
    assert db_rows(hold_env)[0][2] == "H1"


# ---- guard-denied call: deny, not defer (F3, §2.2 order independence) ---------

def test_guard_denied_call_returns_deny_not_defer(hold_env):
    # A Read of a missing file classifies H1 (outside add_dirs, not a credential
    # shape) but guard.sh blocks it (wq#730 R6) — the pre-run must surface that
    # as a deny and never write a hold.
    p = run_hook("Read", {"file_path": "/etc/hold-gate-no-such-file.conf"}, hold_env)
    assert decision_of(p) == "deny"
    reason = json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "guard" in reason
    assert db_rows(hold_env) == []


# ---- never holdable (§2.2 closed list) ----------------------------------------

def test_credential_paths_never_held(hold_env):
    # _deny_reason basename glob (*.key) and the guard credential case-block
    # (claude_credentials.md): both fall out of the holdable class entirely.
    for path in ("/etc/ssl/private/server.key",
                 "/home/lumen/minerva/claude_credentials.md"):
        p = run_hook("Read", {"file_path": path}, hold_env)
        assert p.returncode == 0
        assert decision_of(p) != "defer", path
        assert not p.stdout.strip(), path  # no decision at all: today's flow denies
    assert db_rows(hold_env) == []


def test_write_never_held(hold_env):
    for tool, ti in (("Write", {"file_path": "/etc/motd", "content": "x"}),
                     ("Edit", {"file_path": "/etc/motd", "old_string": "a",
                               "new_string": "b"})):
        p = run_hook(tool, ti, hold_env)
        assert p.returncode == 0
        assert not p.stdout.strip(), tool
    assert db_rows(hold_env) == []


def test_inside_add_dirs_read_is_no_decision(hold_env):
    p = run_hook("Read", {"file_path": f"{WORKTREE}/README.md"}, hold_env)
    assert p.returncode == 0 and not p.stdout.strip()


def test_non_read_verb_and_compound_bash_not_held(hold_env):
    for cmd in (f"rsync {OUTSIDE_FILE} /tmp/x",          # unlisted verb
                f"cat {OUTSIDE_FILE} | head -1",          # compound
                f"cat {OUTSIDE_FILE} /etc/fstab"):        # two outside targets
        p = run_hook("Bash", {"command": cmd}, hold_env)
        assert p.returncode == 0
        assert not p.stdout.strip(), cmd
    assert db_rows(hold_env) == []


def test_kill_switch_is_inert(hold_env):
    hold_env["FOREMAN_HOLD_GATE"] = "off"
    p = run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    assert p.returncode == 0 and not p.stdout.strip()


def test_missing_tool_use_id_is_no_decision(hold_env):
    # §2.6: without tool_use_id the approval can't bind to the one pending call.
    p = run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env, tool_use_id="")
    assert p.returncode == 0 and not p.stdout.strip()
    assert db_rows(hold_env) == []


# ---- lower layer: action_gate RED → deny (F3) ---------------------------------

def test_action_gate_red_returns_deny(hold_env, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("FOREMAN_TICKET_ID", "4760")
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", WORKTREE)
    monkeypatch.setenv("FOREMAN_HOLD_DB", str(tmp_path / "hold.db"))
    monkeypatch.setenv("FOREMAN_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(hold_gate, "guard_denies", lambda raw: None)
    monkeypatch.setattr(hold_gate, "action_gate_denies",
                        lambda t, ti, cwd: ("deny", "BUILTIN_RED test"))
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": f"cat {OUTSIDE_FILE}"},
                          "session_id": "sess-1", "tool_use_id": "toolu_01",
                          "cwd": WORKTREE})
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
    rc = hold_gate.hook_main()
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "action_gate" in out["hookSpecificOutput"]["permissionDecisionReason"]


# ---- fingerprint (§2.6) --------------------------------------------------------

def test_fingerprint_differs_on_tool_use_id():
    ic = hold_gate.canonical_input("Read", {"file_path": OUTSIDE_FILE})
    f1 = hold_gate.fingerprint("4760", "sess-1", "toolu_01", "Read", ic, "H1",
                               OUTSIDE_FILE)
    f2 = hold_gate.fingerprint("4760", "sess-1", "toolu_02", "Read", ic, "H1",
                               OUTSIDE_FILE)
    assert f1 != f2
    # reproducible for the SAME call (resume re-fires the identical envelope)
    assert f1 == hold_gate.fingerprint("4760", "sess-1", "toolu_01", "Read", ic,
                                       "H1", OUTSIDE_FILE)


def test_fingerprint_normalizes_bash_whitespace():
    i1 = hold_gate.canonical_input("Bash", {"command": f"cat   {OUTSIDE_FILE}"})
    i2 = hold_gate.canonical_input("Bash", {"command": f"cat {OUTSIDE_FILE}"})
    assert i1 == i2


# ---- F1: internal error never defers -------------------------------------------

def test_unreadable_db_is_nonblocking_error_not_defer(hold_env, tmp_path):
    hold_env["FOREMAN_HOLD_DB"] = str(tmp_path)  # a directory: sqlite can't open it
    p = run_hook("Read", {"file_path": OUTSIDE_FILE}, hold_env)
    assert p.returncode == 1  # non-blocking hook error (F1)
    assert not p.stdout.strip()
