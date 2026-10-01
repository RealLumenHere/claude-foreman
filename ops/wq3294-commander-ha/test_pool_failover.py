"""Tests for pool_failover.py (wq#3294 item B) and mcp_probe.py (item D helper).

Probe tests run against real local HTTP servers (healthy SSE-framed, healthy JSON,
error-body, dead port) — the probe is exercised through urllib exactly as in
production, not mocked. Transition tests drive tick() with scripted probe results
to prove hysteresis, hook firing, and state persistence across restarts.
"""

import http.server
import json
import socket
import threading

import pytest

import mcp_probe
import pool_failover


# ---------- local backend stand-ins ----------

class _Handler(http.server.BaseHTTPRequestHandler):
    mode = "json_result"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.mode == "http_500":
            self.send_response(500)
            self.end_headers()
            return
        if self.mode == "sse_result":
            body = (
                'event: message\n'
                'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
            ).encode()
            ctype = "text/event-stream"
        elif self.mode == "json_error":
            body = json.dumps(
                {"jsonrpc": "2.0", "id": 1,
                 "error": {"code": -32000, "message": "DC child crashed"}}
            ).encode()
            ctype = "application/json"
        else:  # json_result
            body = json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
            ).encode()
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def backend():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def _url(srv):
    return f"http://127.0.0.1:{srv.server_address[1]}/mcp"


def _dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------- probe_backend against real HTTP ----------

def test_probe_healthy_json(backend):
    _Handler.mode = "json_result"
    assert pool_failover.probe_backend(_url(backend), 5) is True


def test_probe_healthy_sse(backend):
    _Handler.mode = "sse_result"
    assert pool_failover.probe_backend(_url(backend), 5) is True


def test_probe_200_with_jsonrpc_error_is_unhealthy(backend):
    # A live gateway fronting a crash-looping DC answers 200 + error body —
    # the exact outage mode the loop exists to catch.
    _Handler.mode = "json_error"
    assert pool_failover.probe_backend(_url(backend), 5) is False


def test_probe_http_500_is_unhealthy(backend):
    _Handler.mode = "http_500"
    assert pool_failover.probe_backend(_url(backend), 5) is False


def test_probe_dead_port_is_unhealthy():
    url = f"http://127.0.0.1:{_dead_port()}/mcp"
    assert pool_failover.probe_backend(url, 2) is False


# ---------- mcp_probe e2e helper ----------

def test_mcp_probe_pass_and_fail(backend, capsys):
    _Handler.mode = "json_result"
    assert mcp_probe.main(["mcp_probe", _url(backend)]) == 0
    _Handler.mode = "http_500"
    assert mcp_probe.main(["mcp_probe", _url(backend)]) == 1
    assert mcp_probe.main(["mcp_probe", f"http://127.0.0.1:{_dead_port()}/mcp"]) == 1


# ---------- tick(): hysteresis + hooks ----------

def _cfg(**over):
    cfg = {
        "backends": [{"name": "b1", "url": "http://x/mcp"}],
        "fail_threshold": 3,
        "rise_threshold": 2,
    }
    cfg.update(over)
    return cfg


def _drive(results, cfg=None):
    """Run tick once per scripted probe result; return (events, hook_calls, state)."""
    cfg = cfg or _cfg()
    state, events, hooks = {}, [], []
    it = iter(results)
    probe = lambda url, t: next(it)
    hook = lambda template, name, url, event: hooks.append((name, event))
    for _ in results:
        events += pool_failover.tick(cfg, state, probe=probe, hook=hook)
    return events, hooks, state


def test_single_blip_does_not_demote():
    # One 70s abort (wq#3264 Mode B) must not flap the pool.
    events, hooks, state = _drive([True, False, True, True])
    assert events == [] and hooks == []
    assert state["b1"]["healthy"] is True


def test_sustained_failure_demotes_exactly_once():
    events, hooks, state = _drive([False] * 5)
    assert events == [("b1", "demote")]
    assert hooks == [("b1", "demote")]
    assert state["b1"]["healthy"] is False


def test_recovery_promotes_after_rise_threshold():
    events, hooks, _ = _drive([False, False, False, True, True])
    assert events == [("b1", "demote"), ("b1", "promote")]
    assert hooks[-1] == ("b1", "promote")


def test_state_survives_restart_without_refiring(tmp_path):
    # Demote, persist, reload into a fresh process: continued failure must not
    # fire a second demote hook.
    cfg = _cfg()
    events, _, state = _drive([False, False, False])
    assert events == [("b1", "demote")]
    p = str(tmp_path / "state.json")
    pool_failover.save_state(p, state)
    reloaded = pool_failover.load_json(p, {})
    events2, hooks2 = _drive_from(reloaded, [False, False], cfg)
    assert events2 == [] and hooks2 == []


def _drive_from(state, results, cfg):
    events, hooks = [], []
    it = iter(results)
    probe = lambda url, t: next(it)
    hook = lambda template, name, url, event: hooks.append((name, event))
    for _ in results:
        events += pool_failover.tick(cfg, state, probe=probe, hook=hook)
    return events, hooks


def test_two_backends_independent():
    cfg = _cfg(backends=[{"name": "lumen-dc", "url": "http://l/mcp"},
                         {"name": "bes-dc", "url": "http://b/mcp"}])
    state, hooks = {}, []
    # lumen-dc always fails, bes-dc always healthy
    probe = lambda url, t: url == "http://b/mcp"
    hook = lambda template, name, url, event: hooks.append((name, event))
    events = []
    for _ in range(4):
        events += pool_failover.tick(cfg, state, probe=probe, hook=hook)
    assert events == [("lumen-dc", "demote")]
    assert state["bes-dc"]["healthy"] is True


def test_hook_substitution_and_failure_is_nonfatal(tmp_path):
    marker = tmp_path / "hook-ran"
    pool_failover.run_hook(["touch", str(marker)], "b1", "http://x", "demote")
    assert marker.exists()
    # Missing binary must not raise (loop must never die on a paging failure).
    pool_failover.run_hook(["/nonexistent/pager", "{name}"], "b1", "http://x", "demote")


def test_main_once_mode(backend, tmp_path):
    _Handler.mode = "json_result"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "backends": [{"name": "b1", "url": _url(backend)}],
        "fail_threshold": 1, "rise_threshold": 1, "probe_timeout_s": 5,
    }))
    st = tmp_path / "state.json"
    assert pool_failover.main(["--config", str(cfg), "--state", str(st), "--once"]) == 0
    assert json.loads(st.read_text())["b1"]["healthy"] is True


def test_main_bad_config(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text("{}")
    st = tmp_path / "state.json"
    assert pool_failover.main(["--config", str(cfg), "--state", str(st), "--once"]) == 2
