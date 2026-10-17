"""wq#4141 -- tests for the flashrom MCP server."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))
import server  # noqa: E402


def test_initialize():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["serverInfo"]["name"] == "limpet-flashrom-mcp"
    assert "tools" in resp["result"]["capabilities"]


def test_tools_list():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = resp["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "flashrom_probe" in names
    assert "flashrom_read" in names
    assert "flashrom_write" in names
    assert "flashrom_erase" in names
    assert "flashrom_verify" in names
    assert len(tools) == 5


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
