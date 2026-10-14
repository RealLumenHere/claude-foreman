import json
import os
import subprocess
import sys
import time

import pytest

from meta_server import MetaServer, namespaced

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.dirname(HERE)
FIXTURE_CHILD = os.path.join(HERE, "fixture_child_server.py")


def _manifest(tmp_path, include_cli=True, include_second_cli=False):
    data = {
        "mcp_servers": [
            {
                "name": "fixture",
                "transport": "stdio",
                "command": [sys.executable, FIXTURE_CHILD],
                "enabled": True,
            }
        ],
        "cli_tools": [],
    }
    if include_cli:
        data["cli_tools"].append(
            {"name": "echoer", "command": ["/bin/echo", "{message}"], "description": "echoes"}
        )
    if include_second_cli:
        data["cli_tools"].append(
            {"name": "truthy", "command": ["/bin/true"], "description": "always succeeds"}
        )
    path = tmp_path / "manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return str(path)


@pytest.fixture
def server(tmp_path):
    manifest_path = _manifest(tmp_path)
    srv = MetaServer(manifest_path, reload_poll_sec=0.05)
    yield srv
    for client in srv.registry._clients.values():
        client.stop()


def test_discovers_proxied_and_cli_tools(server):
    tools = server.registry.snapshot()
    assert namespaced("fixture", "ping") in tools
    assert namespaced("cli", "echoer") in tools


def test_tools_list_over_jsonrpc(server):
    response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in response["result"]["tools"]}
    assert namespaced("fixture", "ping") in names
    assert namespaced("cli", "echoer") in names


def test_tools_call_proxies_to_child_server(server):
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": namespaced("fixture", "ping"), "arguments": {}},
        }
    )
    assert response["result"] == {"reply": "pong"}


def test_tools_call_runs_cli_tool(server):
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": namespaced("cli", "echoer"), "arguments": {"message": "hi"}},
        }
    )
    assert response["result"]["stdout"].strip() == "hi"


def test_unknown_tool_returns_jsonrpc_error(server):
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    assert response["error"]["code"] == -32601


def test_hot_reload_adds_and_removes_tools_without_restart(tmp_path):
    manifest_path = _manifest(tmp_path, include_cli=True, include_second_cli=False)
    srv = MetaServer(manifest_path, reload_poll_sec=0.05)
    try:
        assert namespaced("cli", "truthy") not in srv.registry.snapshot()

        time.sleep(0.02)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "mcp_servers": [
                        {
                            "name": "fixture",
                            "transport": "stdio",
                            "command": [sys.executable, FIXTURE_CHILD],
                            "enabled": True,
                        }
                    ],
                    "cli_tools": [
                        {"name": "truthy", "command": ["/bin/true"], "description": "always succeeds"}
                    ],
                },
                f,
            )

        new_manifest = srv.watcher.poll()
        assert new_manifest is not None
        srv.registry.rebuild(new_manifest)

        tools = srv.registry.snapshot()
        assert namespaced("cli", "truthy") in tools
        assert namespaced("cli", "echoer") not in tools
        # the still-configured child server survives a reload untouched
        assert namespaced("fixture", "ping") in tools
    finally:
        for client in srv.registry._clients.values():
            client.stop()


def test_end_to_end_subprocess_speaks_mcp_over_stdio(tmp_path):
    """Spawns meta_server.py itself and drives it exactly as an agent would:
    newline-delimited JSON-RPC over stdin/stdout. This is the real upstream
    protocol surface, not just the in-process handle_request() shortcut.
    """
    manifest_path = _manifest(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(SERVER_DIR, "meta_server.py"), manifest_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        def send(msg):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()

        def recv():
            line = proc.stdout.readline()
            assert line, f"server exited early; stderr={proc.stderr.read()}"
            return json.loads(line)

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        init_resp = recv()
        assert init_resp["result"]["serverInfo"]["name"] == "limpet-meta-server"

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        list_resp = recv()
        names = {t["name"] for t in list_resp["result"]["tools"]}
        assert namespaced("fixture", "ping") in names

        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": namespaced("fixture", "ping"), "arguments": {}},
            }
        )
        call_resp = recv()
        assert call_resp["result"] == {"reply": "pong"}
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
