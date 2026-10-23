"""wq#4507 -- tests for the agent chat TUI module (readline MVP + shared session).

Textual itself isn't installed in this environment (no pip on the worker
allowlist), which is exactly the fallback path the ticket requires: these
tests exercise AgentSession, the workspace/status helpers, and the readline
MVP loop directly. A smoke test for the Textual App is included but skips
cleanly via `pytest.importorskip` when the optional dependency is absent.
"""
import io
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_tui as atui  # noqa: E402
import workspace_console as wcon  # noqa: E402


def _git(ws, *args):
    subprocess.run(
        ["git", "-C", ws, *args], check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
             "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"},
    )


def _evidence_block(seq, tool, ok=True, coverage=None):
    return {
        "operation": tool, "tool": tool,
        "started_at": f"2026-01-01T00:0{seq}:00.000000Z",
        "finished_at": f"2026-01-01T00:0{seq}:01.000000Z",
        "ok": ok, "coverage": coverage, "untrusted_content": False,
        "evidence_id": f"ev_{seq:064d}",
    }


def _append_evidence(ws, block):
    path = wcon.log_path(ws)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(block, sort_keys=True) + "\n")


# -- manifest / git / header -------------------------------------------------

def test_read_manifest_missing_returns_empty_dict(tmp_path):
    assert atui.read_manifest(str(tmp_path)) == {}


def test_read_manifest_parses_existing_file(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"target_id": "acme-router-v2"}))
    assert atui.read_manifest(str(tmp_path))["target_id"] == "acme-router-v2"


def test_git_state_non_git_dir(tmp_path):
    assert atui.git_state(str(tmp_path)) == {"head": None, "dirty": None}


def test_git_state_clean_then_dirty(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(str(ws), "init", "-q")
    (ws / "f.txt").write_text("x")
    _git(str(ws), "add", "f.txt")
    _git(str(ws), "commit", "-q", "-m", "init")

    state = atui.git_state(str(ws))
    assert state["head"] is not None
    assert state["dirty"] is False

    (ws / "f.txt").write_text("y")
    assert atui.git_state(str(ws))["dirty"] is True


def test_workspace_header_combines_manifest_and_git(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    manifest = {
        "target_id": "acme-router-v2",
        "identity": {"vendor": "Acme"},
        "hardware_bound": [{"capability": "debug_probe", "adapter": "ST-Link/V2"}],
    }
    (ws / "manifest.json").write_text(json.dumps(manifest))

    header = atui.workspace_header(str(ws))
    assert header["target_id"] == "acme-router-v2"
    assert header["hardware_bound"][0]["adapter"] == "ST-Link/V2"
    assert header["git_head"] is None  # no .git dir in this fixture


def test_status_from_env_defaults_are_honest_not_guessed(tmp_path, monkeypatch):
    monkeypatch.delenv("LIMPET_MODEL", raising=False)
    monkeypatch.delenv("LIMPET_PROVIDER", raising=False)
    status = atui.status_from_env(str(tmp_path))
    assert status.model == "unset"
    assert status.provider == "unset"
    assert status.devices == []


def test_status_from_env_uses_explicit_args_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMPET_MODEL", "from-env")
    status = atui.status_from_env(str(tmp_path), model="from-arg")
    assert status.model == "from-arg"


def test_status_from_env_devices_from_hardware_bound(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "hardware_bound": [{"capability": "debug_probe", "adapter": "ST-Link/V2"},
                            {"capability": "sdr"}],
    }))
    status = atui.status_from_env(str(tmp_path))
    assert status.devices == ["ST-Link/V2", "sdr"]


def test_status_render_shape():
    status = atui.StatusInfo(model="m", provider="p", devices=["ST-Link/V2"])
    assert status.render() == "model: m  provider: p  devices: ST-Link/V2"


# -- tool panel reuse ---------------------------------------------------------

def test_tool_rows_reuses_workspace_console_projection(tmp_path):
    ws = str(tmp_path)
    _append_evidence(ws, _evidence_block(1, "binwalk", ok=True, coverage="scanned"))
    _append_evidence(ws, _evidence_block(2, "unblob", ok=False))

    rows = atui.tool_rows(ws)
    assert [r["tool"] for r in rows] == ["binwalk", "unblob"]
    assert rows[0]["ok"] is True
    assert rows[1]["ok"] is False


# -- AgentSession -------------------------------------------------------------

def test_reply_stream_without_backend_is_honest(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    session = atui.AgentSession(str(ws))

    chunks = list(session.reply_stream("hello"))
    assert chunks == [atui.NO_BACKEND_NOTICE]
    assert [t.role for t in session.turns] == ["user", "agent"]
    assert session.turns[1].text == atui.NO_BACKEND_NOTICE
    assert session.turns[1].done is True


def test_reply_stream_with_backend_streams_and_logs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    session = atui.AgentSession(str(ws), backend=lambda text: iter(["Hello ", "world"]))

    chunks = list(session.reply_stream("hi"))
    assert chunks == ["Hello ", "world"]
    assert session.turns[1].text == "Hello world"

    log_path = ws / "notes" / "session.jsonl"
    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert kinds.count("user_turn") == 1
    assert kinds.count("agent_message") == 3  # 2 deltas + 1 final done=True
    assert events[0]["text"] == "hi"
    assert events[-1]["done"] is True
    assert events[-1]["text"] == "Hello world"


def test_append_log_is_noop_when_workspace_missing(tmp_path):
    missing = tmp_path / "does-not-exist"
    session = atui.AgentSession(str(missing))
    chunks = list(session.reply_stream("hi"))
    assert chunks == [atui.NO_BACKEND_NOTICE]
    assert not missing.exists()


# -- readline MVP fallback -----------------------------------------------------

def test_run_readline_echoes_status_and_tool_rows_then_exits(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _append_evidence(str(ws), _evidence_block(1, "binwalk"))
    session = atui.AgentSession(str(ws))
    status = atui.StatusInfo(model="m", provider="p", devices=[])
    out = io.StringIO()
    inputs = iter(["exit"])

    atui.run_readline(session, status, input_fn=lambda prompt: next(inputs), out=out)

    printed = out.getvalue()
    assert "model: m  provider: p" in printed
    assert "binwalk" in printed


def test_run_readline_sends_message_through_session(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    session = atui.AgentSession(str(ws), backend=lambda text: iter([f"echo:{text}"]))
    status = atui.StatusInfo(model="m", provider="p", devices=[])
    out = io.StringIO()
    inputs = iter(["hi", "exit"])

    atui.run_readline(session, status, input_fn=lambda prompt: next(inputs), out=out)

    assert "echo:hi" in out.getvalue()
    assert session.turns[-1].text == "echo:hi"


def test_run_readline_handles_eof_cleanly(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    session = atui.AgentSession(str(ws))
    status = atui.StatusInfo(model="m", provider="p", devices=[])

    def _raise_eof(prompt):
        raise EOFError

    atui.run_readline(session, status, input_fn=_raise_eof, out=io.StringIO())  # must not raise


# -- Textual smoke (skips when the optional dependency is absent) -------------

def test_textual_app_smoke(tmp_path):
    pytest.importorskip("textual")
    ws = tmp_path / "ws"
    ws.mkdir()
    session = atui.AgentSession(str(ws))
    status = atui.StatusInfo(model="m", provider="p", devices=[])
    app = atui.LimpetChatApp(session, status)
    assert app is not None
