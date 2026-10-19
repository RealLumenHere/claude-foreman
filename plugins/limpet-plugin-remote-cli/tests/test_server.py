"""Tests for the remote-cli MCP server (wq#4396, corrected under wq#4398).

Three layers, in the order they catch things:

1. JSON-RPC shape tests (no socket) — mirror limpet-plugin-flashrom's suite.
2. Wire-protocol conformance against a fake daemon that speaks remote-cli's
   REAL protocol, pinned by tests/fixtures/remote_cli_protocol.json. The fake
   dispatches on "cmd" and rejects anything else exactly as remote-cli does,
   so a wrapper that invents a command fails here instead of shipping green.
3. Live integration against the actual remote-cli binary — the probe wq#4396
   never ran. wq#4397 ran it by hand and got
   `{'ok': False, 'error': 'unknown cmd None'}` from all four tools; these
   tests make that probe permanent and re-runnable.

Layer 2 exists because wq#4396's original suite was 8/8 green while 0/4 tools
worked: its fake dispatched on the same invented "op" key the wrapper sent, so
it could not fail. Same remedy shape as ISSUE-LOG row 1309 (wq#3881's OpenOCD
MCP build): a fake that speaks the REAL wire protocol over a real socket.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))
import server  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "remote_cli_protocol.json")
with open(FIXTURE) as fh:
    PROTOCOL = json.load(fh)

REMOTE_CLI_BIN = os.environ.get("REMOTE_CLI_BIN", "/home/lumen/bin/remote-cli")
if not os.path.exists(REMOTE_CLI_BIN):
    REMOTE_CLI_BIN = shutil.which("remote-cli") or ""
needs_remote_cli = pytest.mark.skipif(
    not REMOTE_CLI_BIN,
    reason="real remote-cli binary not found (set REMOTE_CLI_BIN to run the live layer)")


# --------------------------------------------------------------------------
# 1. JSON-RPC shape
# --------------------------------------------------------------------------

def test_initialize():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["serverInfo"]["name"] == "limpet-remote-cli-mcp"
    assert "tools" in resp["result"]["capabilities"]


def test_tools_list():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = resp["result"]["tools"]
    names = [t["name"] for t in tools]
    assert sorted(names) == ["exec", "logs", "send", "snapshot"]


def test_unknown_tool():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "nonexistent", "arguments": {}}})
    assert "error" in resp


def test_initialized_notification():
    resp = server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    assert resp is None


def test_unknown_method():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 5, "method": "foo/bar", "params": {}})
    assert "error" in resp


def test_exec_unreachable_socket_returns_error_not_exception():
    missing = os.path.join(tempfile.gettempdir(), "wq4398-no-such-remote-cli.sock")
    client = server.RemoteCliClient(socket_path=missing, timeout=1.0)
    with pytest.raises(server.RemoteCliError, match="cannot reach remote-cli"):
        client.connect()


# --------------------------------------------------------------------------
# 2. Wire-protocol conformance
# --------------------------------------------------------------------------

class FakeRemoteCli:
    """Fake daemon speaking remote-cli's real protocol: dispatch on the "cmd"
    key over the five commands the fixture pins, reply `unknown cmd <repr>` to
    anything else (remote-cli:292). Serves many connections, since one
    tool_exec call makes several. Records every request for assertions."""

    def __init__(self, scrollback="", delay=0.0):
        self._dir = tempfile.mkdtemp(prefix="wq4398-fake-remote-cli-")
        self.socket_path = os.path.join(self._dir, "shared.sock")
        self.requests = []
        self.scrollback = scrollback
        self.delay = delay
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.socket_path)
        self._sock.listen(8)
        self._stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            req = json.loads(data.decode())
            self.requests.append(req)
            if self.delay:
                time.sleep(self.delay)
            try:
                conn.sendall((json.dumps(self._reply(req)) + "\n").encode())
            except OSError:
                pass

    def _reply(self, req):
        cmd = req.get("cmd")
        if cmd == "snapshot":
            return {"ok": True, "screen": "fake$ ", "rows": 24, "cols": 80}
        if cmd == "scrollback":
            lines = int(req.get("lines", 0))
            text = self.scrollback
            if lines > 0:
                text = "\n".join(text.splitlines()[-lines:])
            return {"ok": True, "text": text}
        if cmd == "send_keys":
            return {"ok": True}
        if cmd == "resize":
            return {"ok": True}
        if cmd == "status":
            return {"ok": True, "status": {"alive": True}}
        return {"ok": False, "error": f"unknown cmd {cmd!r}"}

    def close(self):
        self._stop.set()
        self._sock.close()


def _with_fake(monkeypatch, **kwargs):
    fake = FakeRemoteCli(**kwargs)
    monkeypatch.setattr(server, "SOCKET_PATH", fake.socket_path)
    return fake


def test_fixture_pins_cmd_key_not_op():
    assert PROTOCOL["request_key"] == "cmd"
    assert "exec" not in PROTOCOL["commands"]


def test_client_sends_cmd_key(monkeypatch):
    fake = _with_fake(monkeypatch)
    try:
        assert server.tool_snapshot()["ok"] is True
        assert fake.requests == [{"cmd": "snapshot"}]
    finally:
        fake.close()


def test_every_tool_uses_only_real_protocol_commands(monkeypatch):
    """The assertion wq#4396's suite was missing: every command this wrapper
    puts on the wire must be one remote-cli actually implements."""
    fake = _with_fake(monkeypatch, scrollback="")
    try:
        server.tool_snapshot()
        server.tool_logs(lines=5)
        server.tool_send("\x03")
        server.tool_exec("true", timeout=0.6)  # times out: no shell to answer
        assert fake.requests, "fake recorded no requests"
        for req in fake.requests:
            assert "op" not in req, f"wrapper still speaks the invented 'op' key: {req}"
            assert req.get("cmd") in PROTOCOL["commands"], f"not a remote-cli command: {req}"
            for field in req:
                if field == "cmd":
                    continue
                assert field in PROTOCOL["commands"][req["cmd"]]["fields"], \
                    f"{req['cmd']} has no field {field!r} in the real protocol"
    finally:
        fake.close()


def test_logs_maps_to_scrollback(monkeypatch):
    fake = _with_fake(monkeypatch, scrollback="line one\nline two\nline three")
    try:
        assert server.tool_logs(lines=2)["text"] == "line two\nline three"
        assert fake.requests == [{"cmd": "scrollback", "lines": 2}]
    finally:
        fake.close()


def test_send_maps_to_send_keys_text(monkeypatch):
    fake = _with_fake(monkeypatch)
    try:
        assert server.tool_send("\x03")["ok"] is True
        assert fake.requests == [{"cmd": "send_keys", "text": "\x03"}]
    finally:
        fake.close()


def test_caller_timeout_reaches_the_socket(monkeypatch):
    """wq#4397 finding 6: `timeout` used to be sent as a JSON field and never
    applied to the socket, so every call was capped at the hardcoded 5s."""
    seen = {}
    real_init = server.RemoteCliClient.__init__

    def spy(self, socket_path=None, timeout=server.REQUEST_TIMEOUT):
        seen["timeout"] = timeout
        real_init(self, socket_path=socket_path, timeout=timeout)

    fake = _with_fake(monkeypatch)
    monkeypatch.setattr(server.RemoteCliClient, "__init__", spy)
    try:
        server._call("status", timeout=11.5)
        assert seen["timeout"] == 11.5
        assert "timeout" not in fake.requests[0], "timeout must not be a wire field"
    finally:
        fake.close()


# --------------------------------------------------------------------------
# 2b. exec's marker protocol, as pure functions
# --------------------------------------------------------------------------

def test_exec_payload_echo_cannot_match_its_own_marker():
    payload = server._exec_payload("ls -la", "deadbeef0001")
    assert not server._exec_marker("deadbeef0001").search(payload), \
        "the shell's echo of the payload would be mistaken for the completion marker"
    assert payload.endswith("\n")


def test_exec_marker_parses_exit_code():
    m = server._exec_marker("deadbeef0002").search("RCDONE-deadbeef0002-42\r\n")
    assert m and int(m.group(1)) == 42


def test_exec_output_carves_command_output_from_scrollback():
    token = "deadbeef0003"
    scrollback = (
        "earlier$ whoami\r\nlumen\r\n"
        f"earlier$ echo hi; __rc_status=$?; printf '%s%s-%s-%s\\n' RC DONE {token} \"$__rc_status\"\r\n"
        "hi\r\n"
        f"RCDONE-{token}-0\r\n"
        "earlier$ "
    )
    assert server._exec_output(scrollback, token) == "hi"


def test_exec_output_tolerates_echo_suppressed_session():
    token = "deadbeef0004"
    assert server._exec_output(f"RCDONE-{token}-0\r\n", token) == ""


def test_exec_times_out_without_a_marker(monkeypatch):
    fake = _with_fake(monkeypatch, scrollback="nothing resembling a marker\n")
    try:
        started = time.monotonic()
        result = server.tool_exec("sleep 99", timeout=1.0)
        elapsed = time.monotonic() - started
        assert result["timed_out"] is True
        assert result["ok"] is False
        assert 1.0 <= elapsed < 3.0, f"timeout not honored: returned after {elapsed:.2f}s"
    finally:
        fake.close()


def test_exec_reports_exit_code_once_the_marker_lands(monkeypatch):
    token_holder = {}
    fake = _with_fake(monkeypatch, scrollback="")
    real_payload = server._exec_payload

    def capture(command, token):
        token_holder["token"] = token
        text = real_payload(command, token)
        fake.scrollback = f"$ {text}boom\r\nRCDONE-{token}-7\r\n$ "
        return text

    monkeypatch.setattr(server, "_exec_payload", capture)
    try:
        result = server.tool_exec("false; echo boom", timeout=5.0)
        assert result["ok"] is True
        assert result["exit_code"] == 7
        assert result["output"] == "boom"
    finally:
        fake.close()


# --------------------------------------------------------------------------
# 3. Live integration against the real remote-cli daemon
# --------------------------------------------------------------------------

class LiveRemoteCli:
    """A real `remote-cli serve` process with a real PTY running bash."""

    def __init__(self):
        self._dir = tempfile.mkdtemp(prefix="wq4398-live-remote-cli-")
        self.socket_path = os.path.join(self._dir, "shared.sock")
        self.proc = subprocess.Popen(
            [sys.executable, REMOTE_CLI_BIN, "serve", "--socket", self.socket_path,
             "--cmd", "/bin/bash", "--norc", "--noprofile"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if os.path.exists(self.socket_path):
                time.sleep(0.4)  # let bash reach its first prompt
                return
            time.sleep(0.05)
        self.close()
        raise AssertionError("real remote-cli never created its socket")

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def live(monkeypatch):
    daemon = LiveRemoteCli()
    monkeypatch.setattr(server, "SOCKET_PATH", daemon.socket_path)
    try:
        yield daemon
    finally:
        daemon.close()


@needs_remote_cli
def test_fixture_matches_real_remote_cli():
    """Drift guard: if wq#4395's protocol changes, this fails loudly instead of
    letting the wrapper keep speaking a stale contract."""
    with open(REMOTE_CLI_BIN) as fh:
        source = fh.read()
    live_cmds = set(re.findall(r'cmd == "([a-z_]+)"', source))
    assert live_cmds == set(PROTOCOL["commands"]), \
        f"fixture pins {sorted(PROTOCOL['commands'])}, binary implements {sorted(live_cmds)}"
    assert 'req.get("cmd")' in source


@needs_remote_cli
def test_live_snapshot(live):
    result = server.tool_snapshot()
    assert result.get("ok") is True, result
    assert "screen" in result and result["rows"] > 0


@needs_remote_cli
def test_live_logs(live):
    result = server.tool_logs(lines=50)
    assert result.get("ok") is True, result
    assert isinstance(result["text"], str)


@needs_remote_cli
def test_live_send(live):
    marker = "wq4398-live-send-probe"
    assert server.tool_send(f"echo {marker}\n").get("ok") is True
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if marker in server.tool_logs(lines=50).get("text", ""):
            return
        time.sleep(0.2)
    pytest.fail("keystrokes never reached the shared shell")


@needs_remote_cli
def test_live_exec_returns_output_and_zero_exit(live):
    result = server.tool_exec("echo wq4398-live-exec-ok", timeout=20.0)
    assert result.get("ok") is True, result
    assert result["exit_code"] == 0, result
    assert "wq4398-live-exec-ok" in result["output"], result


@needs_remote_cli
def test_live_exec_propagates_nonzero_exit(live):
    result = server.tool_exec("(exit 3)", timeout=20.0)
    assert result.get("ok") is True, result
    assert result["exit_code"] == 3, result


@needs_remote_cli
def test_live_exec_honors_a_timeout_past_the_old_five_second_cap(live):
    """The boundary wq#4397 live-reproduced as broken: a command that finishes
    at ~6s used to fail at 5.01s no matter what the caller asked for. 6s is
    outside REQUEST_TIMEOUT (5.0) and the caller's 20s is outside the old
    hardcoded ceiling, so this probe can genuinely fail."""
    started = time.monotonic()
    result = server.tool_exec("sleep 6; echo wq4398-slow-done", timeout=20.0)
    elapsed = time.monotonic() - started
    assert result.get("ok") is True, result
    assert result["exit_code"] == 0, result
    assert "wq4398-slow-done" in result["output"], result
    assert elapsed > 5.5, f"returned in {elapsed:.2f}s — the command cannot have run"


@needs_remote_cli
def test_live_exec_short_timeout_gives_up_early(live):
    started = time.monotonic()
    result = server.tool_exec("sleep 30", timeout=2.0)
    elapsed = time.monotonic() - started
    assert result.get("timed_out") is True, result
    assert 2.0 <= elapsed < 4.0, f"timeout not honored: returned after {elapsed:.2f}s"
