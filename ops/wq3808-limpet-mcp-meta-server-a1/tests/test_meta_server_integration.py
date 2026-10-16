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
    assert response["result"]["reply"] == "pong"


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


def _child_manifest(manifest_path, tool_name):
    """A manifest with one stdio child whose command exports `tool_name`."""
    command = [sys.executable, FIXTURE_CHILD]
    if tool_name != "ping":
        command.append(tool_name)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "mcp_servers": [
                    {
                        "name": "fixture",
                        "transport": "stdio",
                        "command": command,
                        "enabled": True,
                    }
                ],
                "cli_tools": [],
            },
            f,
        )


def test_changed_command_restarts_a_still_alive_child(tmp_path):
    """wq#3815 bug 1: pointing a same-named entry at a different command while
    the old child is still alive used to be silently ignored, keeping the stale
    process serving its stale tools.
    """
    manifest_path = str(tmp_path / "manifest.json")
    _child_manifest(manifest_path, "ping")
    srv = MetaServer(manifest_path, reload_poll_sec=0.05)
    try:
        assert namespaced("fixture", "ping") in srv.registry.snapshot()
        old_client = srv.registry._clients["fixture"]
        assert old_client.is_alive()

        time.sleep(0.02)  # make sure the mtime actually moves
        _child_manifest(manifest_path, "ping_b")

        new_manifest = srv.watcher.poll()
        assert new_manifest is not None
        srv.registry.rebuild(new_manifest)

        tools = srv.registry.snapshot()
        assert namespaced("fixture", "ping_b") in tools, "new command's tool not picked up"
        assert namespaced("fixture", "ping") not in tools, "stale tool still served"
        assert not old_client.is_alive(), "stale child process left running"
        assert srv.registry._clients["fixture"] is not old_client
    finally:
        for client in srv.registry._clients.values():
            client.stop()


def test_changed_env_restarts_a_still_alive_child(tmp_path):
    """Same contract as the command case, via the other half of what's baked
    into a running child: its environment.
    """
    manifest_path = str(tmp_path / "manifest.json")

    def write(tool_name):
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "mcp_servers": [
                        {
                            "name": "fixture",
                            "transport": "stdio",
                            "command": [sys.executable, FIXTURE_CHILD],
                            "env": {"FIXTURE_TOOL_NAME": tool_name},
                            "enabled": True,
                        }
                    ],
                    "cli_tools": [],
                },
                f,
            )

    write("ping_env")
    srv = MetaServer(manifest_path, reload_poll_sec=0.05)
    try:
        assert namespaced("fixture", "ping_env") in srv.registry.snapshot()

        time.sleep(0.02)
        write("ping_env2")
        new_manifest = srv.watcher.poll()
        assert new_manifest is not None
        srv.registry.rebuild(new_manifest)

        tools = srv.registry.snapshot()
        assert namespaced("fixture", "ping_env2") in tools
        assert namespaced("fixture", "ping_env") not in tools
    finally:
        for client in srv.registry._clients.values():
            client.stop()


def test_request_reload_forces_an_out_of_cycle_check(tmp_path):
    """wq#3815 bug 2: request_reload() (what SIGHUP calls) set an Event the
    poll loop cleared without ever reading, so an operator got nothing until
    the next full reload_poll_sec tick.
    """
    manifest_path = str(tmp_path / "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"mcp_servers": [], "cli_tools": [{"name": "a", "command": ["/bin/true"]}]}, f)

    srv = MetaServer(manifest_path, reload_poll_sec=60)
    srv.start_hot_reload()
    try:
        assert namespaced("cli", "b") not in srv.registry.snapshot()

        time.sleep(0.02)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                {"mcp_servers": [], "cli_tools": [{"name": "b", "command": ["/bin/true"]}]}, f
            )
        srv.request_reload()

        deadline = time.monotonic() + 5.0  # 12x short of the 60s poll tick
        while time.monotonic() < deadline:
            if namespaced("cli", "b") in srv.registry.snapshot():
                break
            time.sleep(0.02)
        assert namespaced("cli", "b") in srv.registry.snapshot(), (
            "request_reload() did not force a check well inside the 60s poll interval"
        )
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
        assert init_resp["result"]["serverInfo"]["name"] == "limpet-mcp-meta"

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
        assert call_resp["result"]["reply"] == "pong"
        assert call_resp["result"]["_meta"]["evidence"]["ok"] is True
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
