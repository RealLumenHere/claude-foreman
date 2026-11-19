"""wq#5139: energize-confirm bridge + serial-maksoor allowlist.

Offline: the gate runs over an in-memory fake serial-mcp (same tool names), and tokens come from a
temp ENERGIZE_CONFIRM_DIR written through energize_confirm's own record format, so nothing reaches
Maksoor or a UART. Run with the shared guard venv:
  env -u PYTHONPATH ~/.local/share/mcp/_guard/venv/bin/python -m pytest -q ops/wq5139-serial-maksoor-guard
"""
import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "harness"))
import http_stdio_bridge as bridge  # noqa: E402
import energize_confirm as ec  # noqa: E402

ALLOW = [l.strip() for l in open(os.path.join(HERE, "serial-allow.txt")) if l.strip() and not l.startswith("#")]
HIDDEN = {"configure", "save_profile", "delete_profile", "rollback_profile", "export_log"}
UPSTREAM = set(ALLOW) | HIDDEN  # serial-mcp 0.9.3: 25 tools (live --list-backend, 2026-11-19)


def test_allowlist_shape():
    assert len(ALLOW) == len(set(ALLOW)) == 20
    assert not HIDDEN & set(ALLOW)
    assert bridge.ENERGIZING <= set(ALLOW), "every energizing tool exposed must be on the allowlist"
    for ro in ("list_ports", "list_profiles", "read", "get_log", "get_status", "list_connections"):
        assert ro not in bridge.ENERGIZING


def test_pins_cover_exactly_the_allowlist():
    doc = json.load(open(os.path.join(HERE, "serial-pins.json")))
    assert set(doc["servers"]["serial-maksoor@guarded"]["tools"]) == set(ALLOW)


def test_capabilities_match_action_gate():
    # action_gate.classify_mcp_tool names serial capabilities "serial.<fn>": one token works for both.
    src = open(os.path.join(HERE, "..", "..", "harness", "action_gate.py")).read()
    assert 'capability = f"serial.{fn}"' in src


def fake_backend(calls):
    from fastmcp import FastMCP
    srv = FastMCP("fake-serial-mcp")

    @srv.tool
    def write(connection_id: str, data: str) -> str:
        calls.append(("write", connection_id, data))
        return "wrote"

    @srv.tool
    def read(connection_id: str) -> str:
        calls.append(("read", connection_id))
        return "rx"
    return srv


def gated(calls):
    from fastmcp import FastMCP
    p = FastMCP.as_proxy(fake_backend(calls), name="b")
    p.add_middleware(bridge.make_gate(ec.verify_token))
    return p


def mint(tmp_path, cap, ttl=60):
    tok = "t-" + hashlib.sha256(f"{cap}{time.time()}".encode()).hexdigest()[:24]
    path = os.path.join(ec.TOKEN_DIR, hashlib.sha256(tok.encode()).hexdigest() + ".json")
    with open(path, "w") as f:
        json.dump({"capability": cap, "expires_at": time.time() + ttl}, f)
    return tok


@pytest.fixture(autouse=True)
def token_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "TOKEN_DIR", str(tmp_path))


def run(coro):
    return asyncio.run(coro)


async def call(srv, name, args):
    from fastmcp import Client
    async with Client(srv) as c:
        r = await c.call_tool(name, args, raise_on_error=False)
        return r.is_error, " ".join(getattr(x, "text", "") or "" for x in r.content)


def test_list_adds_token_param_only_to_energizing():
    from fastmcp import Client

    async def go():
        async with Client(gated([])) as c:
            return {t.name: t.inputSchema for t in await c.list_tools()}
    tools = run(go())
    assert "confirm_token" in tools["write"]["properties"]
    assert "confirm_token" not in tools["read"]["properties"]


def test_write_without_token_refused_and_not_forwarded():
    calls = []
    err, text = run(call(gated(calls), "write", {"connection_id": "c", "data": "x"}))
    assert err and "confirm_token" in text and "serial.write" in text
    assert calls == []


def test_write_with_wrong_capability_refused(tmp_path):
    calls = []
    tok = mint(tmp_path, "serial.transact")
    err, _ = run(call(gated(calls), "write", {"connection_id": "c", "data": "x", "confirm_token": tok}))
    assert err and calls == []


def test_write_with_token_forwarded_stripped_and_single_use(tmp_path):
    calls = []
    srv = gated(calls)
    tok = mint(tmp_path, "serial.write")
    err, text = run(call(srv, "write", {"connection_id": "c", "data": "x", "confirm_token": tok}))
    assert not err and "wrote" in text
    assert calls == [("write", "c", "x")]  # backend never saw confirm_token
    err, _ = run(call(srv, "write", {"connection_id": "c", "data": "y", "confirm_token": tok}))
    assert err and len(calls) == 1


def test_expired_token_refused(tmp_path):
    calls = []
    tok = mint(tmp_path, "serial.write", ttl=-1)
    err, _ = run(call(gated(calls), "write", {"connection_id": "c", "data": "x", "confirm_token": tok}))
    assert err and calls == []


def test_read_needs_no_token_and_refuses_one(tmp_path):
    calls = []
    srv = gated(calls)
    err, text = run(call(srv, "read", {"connection_id": "c"}))
    assert not err and "rx" in text
    err, _ = run(call(srv, "read", {"connection_id": "c", "confirm_token": "x"}))
    assert err and calls == [("read", "c")]


def test_launcher_shape():
    out = subprocess.run(["bash", os.path.join(HERE, "install.sh"), "--print-launcher"],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    assert "--pins /home/lumen/.local/share/mcp/serial-maksoor/pins.json" in out
    assert "--mask-output /home/lumen/bin/mask-secrets" in out
    assert "--untrusted-output" in out
    assert "http_stdio_bridge.py http://100.81.204.74:8400/mcp" in out
    for h in HIDDEN:
        assert f",{h}," not in f",{out.split('--allow ')[1].split()[0]},"
