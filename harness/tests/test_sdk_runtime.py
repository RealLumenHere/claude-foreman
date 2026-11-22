"""wq#5211: fworker's `sdk` runtime seam — runtime selection, the per-runtime pool gate,
run_child_sdk's env/argv contract (through the real run_child()), and the uncharged
credit-exhaustion handoff to the CLI. A fake fworker_sdk (real subprocess, stream-json
like the real one) stands in for the model."""
import json
import os
import stat
import sys
import textwrap
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402
import api_credit  # noqa: E402
from classifier import classify, SUCCESS  # noqa: E402

SCOPED = "-".join(("scoped", "fake", "value"))

FAKE_SDK = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    mode = os.environ.get("FAKE_SDK_MODE", "ok")
    with open(os.environ["FAKE_SDK_DUMP"], "w") as f:
        json.dump({"argv": sys.argv[1:], "env": {k: (k in os.environ) for k in (
            "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL", "FWORKER_SDK_API_KEY", "FWORKER_SDK_BASE_URL")},
            "key": os.environ.get("FWORKER_SDK_API_KEY"),
            "base": os.environ.get("FWORKER_SDK_BASE_URL"),
            "attempt": os.environ.get("FOREMAN_ATTEMPT")}, f)
    def emit(x): print(json.dumps(x), flush=True)
    emit({"type": "system", "subtype": "init", "session_id": "sdk-abc", "model": "anthropic/claude-opus-5-5",
          "permissionMode": "acceptEdits"})
    emit({"type": "assistant", "session_id": "sdk-abc", "message": {"role": "assistant",
          "content": [{"type": "text", "text": "working"}], "usage": {"input_tokens": 1, "output_tokens": 1}}})
    if mode == "credit":
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 1,
              "result": "API_CREDIT_EXHAUSTED: Credit balance is too low", "session_id": "sdk-abc",
              "total_cost_usd": 0.01})
        sys.exit(3)
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
          "result": "done\\nTICKET_CLOSED", "session_id": "sdk-abc", "total_cost_usd": 0.02})
""")


@pytest.fixture()
def credit(tmp_path, monkeypatch):
    d = tmp_path / "ac"
    monkeypatch.setattr(api_credit, "STATE_DIR", str(d))
    monkeypatch.setattr(api_credit, "CONFIG_PATH", str(d / "config.json"))
    monkeypatch.setattr(api_credit, "STATE_PATH", str(d / "state.json"))
    monkeypatch.setattr(api_credit, "LEDGER_PATH", str(d / "ledger.jsonl"))
    monkeypatch.setattr(api_credit, "LOCK_PATH", str(d / ".lock"))
    monkeypatch.setattr(api_credit, "_ntfy", lambda *a, **k: None)
    key = tmp_path / "key"
    key.write_text(SCOPED + "\n")
    d.mkdir()
    (d / "config.json").write_text(json.dumps({
        "enabled": True, "key_file": str(key), "base_url": "http://127.0.0.1:4000",
        "grant_schedule": [{"from": "2020-01-13", "grant_usd": 200}]}))
    monkeypatch.delenv("FOREMAN_RUNTIME", raising=False)
    monkeypatch.setattr(fworker.park_mod, "enabled", lambda: False)
    # other suites repoint fworker.STOP_FILE at a tmp STOP they created; this one must not see it
    monkeypatch.setattr(fworker, "STOP_FILE", str(tmp_path / "no-STOP"))
    return types.SimpleNamespace(key=key, dir=d)


def _args(**kw):
    a = dict(ticket=5211, model="claude-opus-5-5", effort="high",
             archetype="standard-engineering", runtime=None, repo="/tmp/r")
    a.update(kw)
    return types.SimpleNamespace(**a)


def _ticket(monkeypatch, **t):
    base = {"status": "open", "title": "build a thing", "notes": "None", "lane": "build",
            "resume_session": "None"}
    base.update(t)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: dict(base))


def test_select_runtime_picks_sdk_for_eligible_ticket(credit, monkeypatch):
    _ticket(monkeypatch)
    a = _args()
    fworker.select_runtime(a)
    assert a.runtime == "sdk" and a.model == "anthropic/claude-opus-5-5"
    assert fworker.runtime_of(a) == "sdk"


@pytest.mark.parametrize("kw,ticket,want_rt,want_model", [
    ({"runtime": "claude"}, {}, "claude", "claude-opus-5-5"),           # explicit CLI wins
    ({"model": "claude-fable-5-2"}, {}, None, "claude-fable-5-2"),      # Fable stays on the CLI
    ({"archetype": "fire-response"}, {}, None, "claude-opus-5-5"),
    ({}, {"title": "x [no-sdk]"}, None, "claude-opus-5-5"),
    ({}, {"resume_session": "c0ffee-cc-session"}, None, "claude-opus-5-5"),  # CC session resumes on CC
])
def test_select_runtime_leaves_cli(credit, monkeypatch, kw, ticket, want_rt, want_model):
    _ticket(monkeypatch, **ticket)
    a = _args(**kw)
    fworker.select_runtime(a)
    assert a.runtime == want_rt and a.model == want_model


def test_explicit_sdk_falls_back_when_credits_gone(credit, monkeypatch):
    _ticket(monkeypatch)
    api_credit.mark_exhausted("credit balance too low")
    a = _args(runtime="sdk")
    fworker.select_runtime(a)
    assert a.runtime == "claude" and a.model == "claude-opus-5-5"
    b = _args()
    fworker.select_runtime(b)
    assert b.runtime is None  # auto-select: CLI default


def test_dispatch_gate_per_runtime(credit, monkeypatch):
    calls = []
    monkeypatch.setattr(fworker, "can_dispatch", lambda m: calls.append(m) or (False, "max pool low"))
    assert fworker.dispatch_gate(_args(runtime="sdk")) == (True, None)
    assert calls == []  # the sdk runtime never asks the Max-pool governor
    assert fworker.dispatch_gate(_args(runtime="claude")) == (False, "max pool low")
    assert fworker.dispatch_gate(_args(runtime="aider")) == (True, None)
    api_credit.mark_exhausted("dry")
    ok, why = fworker.dispatch_gate(_args(runtime="sdk"))
    assert not ok and "api credits unavailable" in why


@pytest.fixture()
def fake_sdk(tmp_path, monkeypatch):
    p = tmp_path / "fake_sdk.py"
    p.write_text(FAKE_SDK)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    dump = tmp_path / "dump.json"
    monkeypatch.setattr(fworker, "SDK_BIN", str(p))
    monkeypatch.setenv("FAKE_SDK_DUMP", str(dump))
    rd = tmp_path / "run"
    rd.mkdir()
    monkeypatch.setattr(fworker, "run_dir", lambda tid: str(rd))
    monkeypatch.setattr(fworker, "wq_heartbeat", lambda tid: None)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"status": "claimed"})
    return dump


def _child_env():
    # values built at runtime: a credential-shaped literal in a committed file arms the
    # mask tripwire (test_masking.py) for anyone who reads it
    env = dict(os.environ)
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
        env[name] = "-".join(("placeholder", name.lower(), "x" * 8))
    env["ANTHROPIC_BASE_URL"] = "http://gateway"
    return env


def test_run_child_sdk_env_argv_and_success(credit, fake_sdk, tmp_path):
    cmd = fworker.build_claude_cmd("PROMPT", "anthropic/claude-opus-5-5", "high",
                                   "/x/settings.json", ["/home/lumen/a"], max_turns=40)
    state, rc, events, killed, _, _ = fworker.run_child_sdk(
        cmd, str(tmp_path), _child_env(), 40, 60, 5211, 2, frame_gap_s=20, post_result_grace_s=5)
    assert killed is None and rc == 0
    assert classify(state, rc) == SUCCESS and state.runtime == "sdk"
    assert state.session_id == "sdk-abc" and not state.api_credit_exhausted
    d = json.loads(fake_sdk.read_text())
    assert d["argv"] == cmd[1:]  # identical claude argv behind the new binary
    assert d["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": False, "ANTHROPIC_API_KEY": False,
                        "ANTHROPIC_AUTH_TOKEN": False, "ANTHROPIC_BASE_URL": False,
                        "FWORKER_SDK_API_KEY": True, "FWORKER_SDK_BASE_URL": True}
    assert d["key"] == SCOPED and d["base"] == "http://127.0.0.1:4000"
    assert d["attempt"] == "2"


def test_credit_exhaustion_is_flagged_and_released_uncharged(credit, fake_sdk, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SDK_MODE", "credit")
    cmd = fworker.build_claude_cmd("P", "anthropic/claude-opus-5-5", None, "/x/s.json", [])
    state, rc, _, killed, _, _ = fworker.run_child_sdk(
        cmd, str(tmp_path), _child_env(), 40, 60, 5211, 1, frame_gap_s=20, post_result_grace_s=5)
    assert rc == 3 and state.api_credit_exhausted
    log = {"notes": [], "released": []}
    monkeypatch.setattr(fworker, "wq", lambda *a, **k: log["notes"].append(a) or
                        types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(fworker, "wq_release", lambda tid: log["released"].append(tid))
    rc2 = fworker.handle_api_credit_exhausted(_args(runtime="sdk"), 5211, 1, state, str(tmp_path))
    assert rc2 == fworker.EXIT_RUNG_FAULT and log["released"] == [5211]
    assert "API_CREDIT_EXHAUSTED" in log["notes"][0][2]
    # a ticket the worker already closed is judged normally, not released
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"status": "done"})
    assert fworker.handle_api_credit_exhausted(_args(), 5211, 1, state, str(tmp_path)) is None


def test_missing_key_file_is_a_clean_failure(credit, fake_sdk, tmp_path):
    credit.key.unlink()
    state, rc, events, killed, _, _ = fworker.run_child_sdk(
        ["claude", "-p", "x"], str(tmp_path), _child_env(), 40, 60, 5211, 1)
    assert rc == 1 and events[0]["type"] == "sdk_error" and state.runtime == "sdk"
    assert not fake_sdk.exists()  # never spawned
