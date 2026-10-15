"""wq#3964: every tools/call answer carries a _meta.evidence block.

Acceptance: (a) every proxied call carries the block, (b) a child that errors
or times out still gets one with ok=false, (c) the plugin sha in the block is
the one on the registry manifest entry. Plus: the block lands in the bound
workspace's evidence log, and nowhere when no workspace is bound.
"""
import json
import os
import sys
import time

import pytest

from evidence import EVIDENCE_LOG_RELPATH, args_hash
from meta_server import MetaServer, namespaced

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_CHILD = os.path.join(HERE, "fixture_child_server.py")
HANG_CHILD = os.path.join(HERE, "fixture_hang_child.py")

CHILD_SHA = "a1" * 20
ECHO_SHA = "b2" * 20
FAIL_SHA = "c3" * 20
HANG_SHA = "d4" * 20


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return str(path)


def _manifest_data(child_sha=CHILD_SHA):
    return {
        "mcp_servers": [
            {
                "name": "fixture",
                "transport": "stdio",
                "command": [sys.executable, FIXTURE_CHILD],
                "git_sha": child_sha,
            },
            {
                "name": "hang",
                "transport": "stdio",
                "command": [sys.executable, HANG_CHILD, "call"],
                "git_sha": HANG_SHA,
                "startup_timeout_sec": 2.0,
                "request_timeout_sec": 0.5,
            },
        ],
        "cli_tools": [
            {
                "name": "echoer",
                "command": ["/bin/echo", "{message}"],
                "git_sha": ECHO_SHA,
                "untrusted_output": False,
            },
            {"name": "failer", "command": ["/bin/false"], "git_sha": FAIL_SHA},
        ],
    }


def _shas(data):
    out = {}
    for e in data["mcp_servers"]:
        out[e["name"]] = e["git_sha"]
    for e in data["cli_tools"]:
        out[namespaced("cli", e["name"])] = e["git_sha"]
    return out


def _sha_for(tool, shas):
    return shas.get(tool) or shas[tool.split(".", 1)[0]]


def _stop(srv):
    for client in srv.registry._clients.values():
        client.stop()


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return str(ws)


@pytest.fixture
def server(tmp_path, workspace):
    srv = MetaServer(_write(tmp_path / "manifest.json", _manifest_data()), workspace=workspace)
    yield srv
    _stop(srv)


def _call(srv, name, arguments, req_id=1):
    return srv.handle_request(
        {"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )


def _block(response):
    if "result" in response:
        return response["result"]["_meta"]["evidence"]
    return response["error"]["data"]["_meta"]["evidence"]


ARGS = {
    namespaced("fixture", "ping"): {},
    namespaced("hang", "never_answers"): {},
    namespaced("cli", "echoer"): {"message": "hi"},
    namespaced("cli", "failer"): {},
}


def _read_log(workspace):
    with open(os.path.join(workspace, EVIDENCE_LOG_RELPATH), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_every_listed_tool_call_carries_evidence_with_registry_sha(server, workspace):
    """(a) + (c): driven off tools/list, so a tool added without a block fails here."""
    listed = server.handle_request({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})
    names = sorted(t["name"] for t in listed["result"]["tools"])
    assert names == sorted(ARGS), "fixture manifest and ARGS drifted apart"
    shas = _shas(_manifest_data())

    blocks = []
    for i, name in enumerate(names, start=1):
        block = _block(_call(server, name, ARGS[name], req_id=i))
        blocks.append(block)
        assert block["operation"] == block["tool"] == name
        assert block["source"]["git_sha"] == _sha_for(name, shas)
        assert block["source"]["server"] == name.split(".", 1)[0]
        assert block["source"]["tool"] == name.split(".", 1)[1]
        assert block["args_sha256"] == args_hash(ARGS[name])
        assert block["started_at"] <= block["finished_at"]
        assert block["evidence_id"].startswith("ev_") and len(block["evidence_id"]) == 3 + 64

    assert _read_log(workspace) == blocks


def test_successful_proxied_call_is_ok_and_keeps_child_result(server):
    response = _call(server, namespaced("fixture", "ping"), {})
    assert response["result"]["reply"] == "pong"
    block = _block(response)
    assert block["ok"] is True and block["error"] is None and block["exit_code"] is None
    assert block["source"]["kind"] == "mcp_server"


def test_child_timeout_still_gets_block_with_ok_false(server, workspace):
    """(b): the child accepts tools/call and never answers."""
    response = _call(server, namespaced("hang", "never_answers"), {})
    assert response["error"]["code"] == -32000
    block = _block(response)
    assert block["ok"] is False
    assert "within" in block["error"]
    assert block["source"]["git_sha"] == HANG_SHA
    assert _read_log(workspace)[-1] == block


def test_cli_error_and_nonzero_exit_get_ok_false(server):
    """(b) for the CLI half: a call that can't run, and one that runs and fails."""
    missing = _block(_call(server, namespaced("cli", "echoer"), {}))
    assert missing["ok"] is False and "missing required argument" in missing["error"]

    failed = _call(server, namespaced("cli", "failer"), {})
    assert failed["result"]["exit_code"] == 1
    block = _block(failed)
    assert block["ok"] is False and block["exit_code"] == 1


def test_child_coverage_and_untrusted_flag(server):
    block = _block(_call(server, namespaced("fixture", "ping"), {"coverage": "partial: 3/5 sections"}))
    assert block["coverage"] == "partial: 3/5 sections"
    assert block["untrusted_content"] is True

    trusted = _block(_call(server, namespaced("cli", "echoer"), {"message": "x"}))
    assert trusted["untrusted_content"] is False
    assert trusted["coverage"] is None


def test_child_meta_is_kept_next_to_evidence(server):
    result = _call(server, namespaced("fixture", "ping"), {"coverage": "full"})["result"]
    assert result["_meta"]["provenance"] == {"untrusted_content": True}
    assert result["_meta"]["coverage"] == "full"


def test_reload_with_new_sha_is_reflected_in_evidence(tmp_path):
    """(c) can fail: a block that read the sha from anywhere but the current
    registry entry (a cached client, a constant) would keep the old value."""
    path = _write(tmp_path / "manifest.json", _manifest_data())
    srv = MetaServer(path)
    try:
        assert _block(_call(srv, namespaced("fixture", "ping"), {}))["source"]["git_sha"] == CHILD_SHA
        new_sha = "e5" * 20
        time.sleep(0.02)
        _write(path, _manifest_data(child_sha=new_sha))
        srv.registry.rebuild(srv.watcher.poll())
        assert _block(_call(srv, namespaced("fixture", "ping"), {}))["source"]["git_sha"] == new_sha
    finally:
        _stop(srv)


def test_no_workspace_bound_writes_no_log(tmp_path, monkeypatch):
    monkeypatch.delenv("LIMPET_WORKSPACE", raising=False)
    srv = MetaServer(_write(tmp_path / "manifest.json", _manifest_data()))
    try:
        assert _block(_call(srv, namespaced("cli", "echoer"), {"message": "x"}))["ok"] is True
        assert srv.evidence_log is None
    finally:
        _stop(srv)


def test_workspace_bound_via_env(tmp_path, monkeypatch, workspace):
    monkeypatch.setenv("LIMPET_WORKSPACE", workspace)
    srv = MetaServer(_write(tmp_path / "manifest.json", _manifest_data()))
    try:
        block = _block(_call(srv, namespaced("cli", "echoer"), {"message": "x"}))
        assert _read_log(workspace) == [block]
    finally:
        _stop(srv)
