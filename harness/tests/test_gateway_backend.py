"""wq#1888 despag P2-2: run_child_gateway() against a REAL despag instance (uvicorn,
background thread, loopback) — not despag/tests' own TestClient (in-process ASGI
transport), because the thing actually under test here is fworker's own HTTP/SSE
client: socket framing, the poll-timeout re-arm, and the dead-port fallback all need
a real socket to be honestly exercised.

claude_max.build_turn is stubbed to spawn despag/tests/fake_backend.py, exactly like
despag's own runner_client fixture (despag/tests/test_uhp.py) — no real Claude
subscription needed, and the SAME fake backend lets a direct run_child() and a
gateway-backed run_child_gateway() be compared on the identical fixture input,
which is the acceptance criterion ("identical classification to the direct path")
in miniature.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import socket
import sys
import threading
import time

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HARNESS_DIR)
sys.path.insert(0, HARNESS_DIR)
sys.path.insert(0, REPO_ROOT)

import fworker  # noqa: E402
from classifier import classify, LIMIT, SUCCESS  # noqa: E402

FAKE_BACKEND = os.path.join(REPO_ROOT, "despag", "tests", "fake_backend.py")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _quick_claude_cmd(model="claude-sonnet-5"):
    """build_claude_cmd()'s exact argv shape — _parse_claude_argv() reads cmd[2] as
    the prompt (cmd[1] == '-p') and the token after '--model'."""
    return ["claude", "-p", "hi there", "--model", model, "--max-turns", "40",
            "--settings", "/dev/null", "--permission-mode", "acceptEdits",
            "--output-format", "stream-json", "--verbose", "--include-hook-events"]


@pytest.fixture()
def despag_server(tmp_path, monkeypatch):
    """Yields (base_url, mode) — `mode` is the same mutable list despag/tests'
    runner_client uses, so a test can flip fake_backend's behaviour (e.g. to
    --mode rate_limited) before making a request."""
    import uvicorn

    monkeypatch.setenv("DESPAG_API_TOKEN_FILE", str(tmp_path / "despag-api.token"))
    monkeypatch.setenv("DESPAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DESPAG_SESSIONS_ROOT", str(tmp_path / "sessions"))

    import despag.app as app_module

    importlib.reload(app_module)

    # ISSUE-LOG #387: despag/admission.py hardcodes MAIN's harness/ onto sys.path,
    # so its LimitObserver builds a classifier.RunState from main's classifier.py,
    # not this worktree's (no to_dict() there yet) — rebind onto this worktree's
    # copy, same fix despag/tests/test_uhp.py's runner_client fixture applies.
    spec = importlib.util.spec_from_file_location(
        "classifier_wq1888_gateway_test", os.path.join(HARNESS_DIR, "classifier.py"))
    fresh_classifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh_classifier)
    monkeypatch.setattr(app_module.admission, "classifier", fresh_classifier)

    monkeypatch.setattr(app_module, "compute_available", lambda h: h.get("enabled", True))
    mode: list[str] = ["--mode", "quick"]
    monkeypatch.setattr(app_module.claude_max, "build_turn",
                        lambda **kw: ([sys.executable, FAKE_BACKEND, *mode], dict(os.environ)))
    monkeypatch.setattr(app_module.admission, "check_admission",
                        lambda model, **kw: {"allow": True, "reasons": ["test stub"],
                                              "retry_after_s": None, "resets_at": None})

    port = _free_port()
    config = uvicorn.Config(app_module.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "despag test server never started"

    monkeypatch.setattr(fworker, "FOREMAN_GATEWAY_TOKEN_FILE",
                         os.environ["DESPAG_API_TOKEN_FILE"])
    try:
        yield f"http://127.0.0.1:{port}", mode
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_gateway_backend_matches_direct_path_classification(despag_server, monkeypatch, tmp_path):
    """The core canary claim: a mechanical-tier turn served through the gateway
    classifies identically to the same fixture run directly (PHASE2_WIRING_SPEC.md
    §B: 'the mapping becomes mechanical' once the sidecar exists)."""
    base_url, _mode = despag_server
    monkeypatch.setattr(fworker, "FOREMAN_GATEWAY_URL", base_url)

    gw_state, gw_exit, gw_events, gw_killed, gw_cpu, gw_pid = fworker.run_child_gateway(
        _quick_claude_cmd(), str(tmp_path), dict(os.environ), 40, 60, 990001, 1)
    assert gw_state.gateway_backend is True
    assert gw_state.gateway_fallback is False
    assert gw_killed is None
    assert gw_pid is None
    assert classify(gw_state, gw_exit) == SUCCESS
    assert gw_state.turns >= 1

    d_state, d_exit, d_events, d_killed, d_cpu, d_pid = fworker.run_child(
        [sys.executable, FAKE_BACKEND, "--mode", "quick"], str(tmp_path), dict(os.environ),
        40, 60, 990001, 1)
    assert classify(d_state, d_exit) == SUCCESS
    assert classify(gw_state, gw_exit) == classify(d_state, d_exit)


def test_gateway_mid_turn_limit_matches_direct_path_classification(despag_server, monkeypatch, tmp_path):
    base_url, mode = despag_server
    mode[:] = ["--mode", "rate_limited"]
    monkeypatch.setattr(fworker, "FOREMAN_GATEWAY_URL", base_url)

    gw_state, gw_exit, gw_events, gw_killed, gw_cpu, gw_pid = fworker.run_child_gateway(
        _quick_claude_cmd(), str(tmp_path), dict(os.environ), 40, 60, 990002, 1)
    assert gw_state.saw_limit is True
    assert gw_state.latest_resets_at == "2026-09-12T13:00:00+00:00"
    assert classify(gw_state, gw_exit) == LIMIT
    # the mid-stream `lumen.limit` event, not just the terminal sidecar, must have
    # produced a synthetic rate_limit_event frame (§B.6 item 2) — not only the
    # terminal one, so a real gateway-backed fworker learns it before turn-end.
    assert any(e.get("type") == "lumen.limit" for e in gw_events)

    d_state, d_exit, *_ = fworker.run_child(
        [sys.executable, FAKE_BACKEND, "--mode", "rate_limited"], str(tmp_path),
        dict(os.environ), 40, 60, 990002, 1)
    assert classify(gw_state, gw_exit) == classify(d_state, d_exit) == LIMIT


def test_gateway_falls_back_to_direct_spawn_on_dead_port(monkeypatch, tmp_path):
    """Ticket's explicit repro shape: point FOREMAN_GATEWAY_URL at a dead port —
    loopback connection refusal, not a hang — and confirm the fallback is both
    functional (the turn still completes, via a direct claude -p-shaped spawn) and
    measurable (gateway_fallback + reason land on the returned state)."""
    monkeypatch.setattr(fworker, "FOREMAN_GATEWAY_URL", "http://127.0.0.1:1")
    token_path = tmp_path / "unused.token"
    token_path.write_text("unused")
    monkeypatch.setattr(fworker, "FOREMAN_GATEWAY_TOKEN_FILE", str(token_path))

    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child_gateway(
        [sys.executable, FAKE_BACKEND, "--mode", "quick"], str(tmp_path), dict(os.environ),
        40, 60, 990003, 1)
    assert state.gateway_backend is False
    assert state.gateway_fallback is True
    assert state.gateway_fallback_reason
    assert classify(state, exit_code) == SUCCESS


def test_gateway_lane_enabled_reads_foreman_gateway_lanes(monkeypatch):
    monkeypatch.delenv("FOREMAN_GATEWAY_LANES", raising=False)
    assert fworker.gateway_lane_enabled("despag-canary") is False
    monkeypatch.setenv("FOREMAN_GATEWAY_LANES", "despag-canary, other-lane")
    assert fworker.gateway_lane_enabled("despag-canary") is True
    assert fworker.gateway_lane_enabled("other-lane") is True
    assert fworker.gateway_lane_enabled("unrelated") is False
    assert fworker.gateway_lane_enabled(None) is False
    assert fworker.gateway_lane_enabled("") is False
