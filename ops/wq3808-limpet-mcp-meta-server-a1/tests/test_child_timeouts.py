"""Regression tests for wq#3815 bug 3 (ruled on in wq#3821): an mcp_servers
child that stops answering must cost only its own entry, never the whole
meta-server.

Each test here fails on the pre-fix code by hanging, so every one is wrapped
in a wall-clock assertion: the point is not just "right answer" but "answer at
all, within a bounded time".
"""
import json
import os
import select
import subprocess
import sys
import time

import pytest

from meta_server import MetaServer, namespaced
from stdio_mcp_client import ChildServerTimeout, StdioMcpClient

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.dirname(HERE)
HANG_CHILD = os.path.join(HERE, "fixture_hang_child.py")

# Deliberately not the 10s/30s defaults: these pin the manifest knob itself,
# so a test passing by accident on the default budget is ruled out.
STARTUP_BUDGET = 0.5
REQUEST_BUDGET = 0.5
# Generous enough that a slow box can't flake it, far short of "forever".
WEDGE_CEILING = 8.0


def _write_manifest(tmp_path, mode, name="manifest.json"):
    """One hung child plus one unrelated, trivially-fast cli_tools entry.

    The cli entry is the load-bearing part: it has nothing to do with the hung
    child, so if it isn't served, the failure wasn't isolated.
    """
    data = {
        "mcp_servers": [
            {
                "name": "hang",
                "transport": "stdio",
                "command": [sys.executable, HANG_CHILD, mode],
                "enabled": True,
                "startup_timeout_sec": STARTUP_BUDGET,
                "request_timeout_sec": REQUEST_BUDGET,
            }
        ],
        "cli_tools": [{"name": "fast", "command": ["/bin/true"], "description": "exits 0"}],
    }
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return str(path)


def _stop_all(srv):
    for client in srv.registry._clients.values():
        client.stop()


def test_child_hung_at_initialize_does_not_wedge_the_server(tmp_path):
    manifest_path = _write_manifest(tmp_path, "initialize")

    started = time.monotonic()
    srv = MetaServer(manifest_path, reload_poll_sec=60)
    elapsed = time.monotonic() - started

    try:
        assert elapsed < WEDGE_CEILING, f"MetaServer() took {elapsed:.1f}s"
        tools = srv.registry.snapshot()
        assert namespaced("cli", "fast") in tools, "unrelated cli tool lost to the hung child"
        assert not [name for name in tools if name.startswith("hang.")]
    finally:
        _stop_all(srv)


def test_hung_child_is_not_left_running(tmp_path):
    """The skipped entry's process must be reaped, not orphaned."""
    client = StdioMcpClient(
        "hang",
        [sys.executable, HANG_CHILD, "initialize"],
        startup_timeout_sec=STARTUP_BUDGET,
    )
    with pytest.raises(ChildServerTimeout):
        client.start()
    assert not client.is_alive()


def test_child_that_goes_silent_mid_call_returns_an_error(tmp_path):
    """Outside the startup-timeout case: the child handshakes fine and only
    stops answering at tools/call, so only the per-request budget can catch it.
    """
    manifest_path = _write_manifest(tmp_path, "call")
    srv = MetaServer(manifest_path, reload_poll_sec=60)
    try:
        assert namespaced("hang", "never_answers") in srv.registry.snapshot()

        started = time.monotonic()
        response = srv.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": namespaced("hang", "never_answers"), "arguments": {}},
            }
        )
        elapsed = time.monotonic() - started

        assert elapsed < WEDGE_CEILING, f"tools/call took {elapsed:.1f}s"
        assert response["error"]["code"] == -32000
        assert "within" in response["error"]["message"]

        # ...and the meta-server is still usable afterwards for everything else.
        ok = srv.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": namespaced("cli", "fast"), "arguments": {}},
            }
        )
        assert ok["result"]["exit_code"] == 0
    finally:
        _stop_all(srv)


def test_child_writing_non_json_is_skipped_not_fatal(tmp_path):
    """A different failure mode than a hang, and one no timeout catches: a
    child that prints a banner on stdout instead of JSON-RPC. It must be
    isolated the same way, not raise out of rebuild().
    """
    data = {
        "mcp_servers": [
            {
                "name": "banner",
                "transport": "stdio",
                "command": ["/bin/echo", "starting up..."],
                "enabled": True,
                "startup_timeout_sec": STARTUP_BUDGET,
            }
        ],
        "cli_tools": [{"name": "fast", "command": ["/bin/true"], "description": "exits 0"}],
    }
    path = tmp_path / "manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    srv = MetaServer(str(path), reload_poll_sec=60)
    try:
        tools = srv.registry.snapshot()
        assert namespaced("cli", "fast") in tools
        assert not [name for name in tools if name.startswith("banner.")]
    finally:
        _stop_all(srv)


def test_end_to_end_subprocess_answers_initialize_despite_hung_child(tmp_path):
    """wq#3815's probe_hang_child.py, as a permanent test: a real meta_server.py
    subprocess must answer a plain initialize even with a hung child configured.
    Pre-fix this got nothing, ever, because __init__'s rebuild() never returned.
    """
    manifest_path = _write_manifest(tmp_path, "initialize")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(SERVER_DIR, "meta_server.py"), manifest_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        )
        proc.stdin.flush()

        ready, _, _ = select.select([proc.stdout], [], [], WEDGE_CEILING)
        assert ready, f"no response within {WEDGE_CEILING}s to a plain initialize"
        init_resp = json.loads(proc.stdout.readline())
        assert init_resp["result"]["serverInfo"]["name"] == "limpet-meta-server"

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        ready, _, _ = select.select([proc.stdout], [], [], WEDGE_CEILING)
        assert ready, "no response to tools/list"
        names = {t["name"] for t in json.loads(proc.stdout.readline())["result"]["tools"]}
        assert namespaced("cli", "fast") in names
        assert not [name for name in names if name.startswith("hang.")]
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
