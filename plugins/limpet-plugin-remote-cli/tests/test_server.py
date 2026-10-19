"""wq#4396 -- tests for the remote-cli MCP server.

Pure JSON-RPC shape tests mirror limpet-plugin-flashrom/tests/test_server.py.
A socket-based integration test runs RemoteCliClient against an in-process
fake Unix-socket remote-cli server (same one-shot request/response wire
shape as classify_access.py's OpenOCDProbe tests) so the wire protocol is
exercised without needing a live remote-cli daemon.
"""
import json
import os
import socket
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))
import server  # noqa: E402


def test_initialize():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["serverInfo"]["name"] == "limpet-remote-cli-mcp"
    assert "tools" in resp["result"]["capabilities"]


def test_tools_list():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = resp["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "exec" in names
    assert "snapshot" in names
    assert "send" in names
    assert "logs" in names
    assert len(tools) == 4


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
    missing = os.path.join(tempfile.gettempdir(), "wq4396-no-such-remote-cli.sock")
    client = server.RemoteCliClient(socket_path=missing, timeout=1.0)
    try:
        client.connect()
        assert False, "expected RemoteCliError on a missing socket"
    except server.RemoteCliError as exc:
        assert "cannot reach remote-cli" in str(exc)


class _FakeRemoteCliServer:
    """One-shot fake remote-cli daemon: accepts one connection, reads one
    JSON line, replies with a canned JSON line keyed on the request's "op"."""

    def __init__(self):
        self._dir = tempfile.mkdtemp(prefix="wq4396-fake-remote-cli-")
        self.socket_path = os.path.join(self._dir, "shared.sock")
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.socket_path)
        self._sock.listen(1)
        self._thread = threading.Thread(target=self._serve_one, daemon=True)
        self._thread.start()

    def _serve_one(self):
        conn, _ = self._sock.accept()
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            request = json.loads(data.decode())
            op = request.get("op")
            if op == "snapshot":
                reply = {"ok": True, "screen": "fake$ "}
            elif op == "logs":
                reply = {"ok": True, "lines": ["line one", "line two"]}
            elif op == "send":
                reply = {"ok": True, "echoed_keys": request.get("keys")}
            elif op == "exec":
                reply = {"ok": True, "command": request.get("command"), "exit_code": 0}
            else:
                reply = {"ok": False, "error": f"unknown op {op!r}"}
            conn.sendall((json.dumps(reply) + "\n").encode())

    def close(self):
        self._sock.close()
        try:
            os.remove(self.socket_path)
        except OSError:
            pass


def test_client_round_trip_snapshot():
    fake = _FakeRemoteCliServer()
    try:
        client = server.RemoteCliClient(socket_path=fake.socket_path, timeout=5.0)
        client.connect()
        reply = client.request("snapshot")
        assert reply == {"ok": True, "screen": "fake$ "}
        client.close()
    finally:
        fake.close()


def test_client_round_trip_send_echoes_keys():
    fake = _FakeRemoteCliServer()
    try:
        client = server.RemoteCliClient(socket_path=fake.socket_path, timeout=5.0)
        client.connect()
        reply = client.request("send", keys="\x03")
        assert reply == {"ok": True, "echoed_keys": "\x03"}
        client.close()
    finally:
        fake.close()
