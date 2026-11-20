"""wq#5167: --call-timeout bounds a hung backend. Real guard subprocess, mock slow backends.

Fuzz audit: 30/720 gitea-mcp calls against a dead upstream hung >10 s and the guard had no
deadline, so the agent hung with them. A call or list that outlives the deadline must come back
as JSON-RPC -32001 within roughly the deadline, write a TIMEOUT audit row, and leave the one
backend session usable for the next call.
"""
import asyncio
import json
import os
import sys
import textwrap
import time
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "lumen_mcp_guard.py")

SLOW_BACKEND = textwrap.dedent('''
    import asyncio, warnings
    warnings.filterwarnings("ignore")
    from fastmcp import FastMCP
    m = FastMCP("slow-dummy")
    @m.tool
    async def slow(seconds: float) -> str:
        await asyncio.sleep(seconds)   # an upstream that never answers, backend still alive
        return "slow-done"
    @m.tool
    def fast(x: str) -> str:
        return "fast:" + x
    m.run(transport="stdio", show_banner=False)
''')

# Answers initialize, then swallows every request: tools/list never comes back.
MUTE_LIST_BACKEND = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mute", "version": "0"}}}) + "\\n")
            sys.stdout.flush()
''')


@pytest.fixture()
def env(tmp_path):
    (tmp_path / "slow.py").write_text(SLOW_BACKEND)
    (tmp_path / "mute.py").write_text(MUTE_LIST_BACKEND)
    e = dict(os.environ)
    e["HOME"] = str(tmp_path)  # isolates the audit log
    return e, str(tmp_path)


def cmd(tmp, backend="slow.py", extra=None, allow="slow,fast"):
    return [sys.executable, GUARD, "--name", "t", "--allow", allow, *(extra or []),
            "--", sys.executable, os.path.join(tmp, backend)]


def run(env_tmp, argv, coro, client_timeout=60):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    e, _ = env_tmp

    async def go():
        async with Client(StdioTransport(command=argv[0], args=argv[1:], env=e),
                          timeout=client_timeout) as c:
            return await coro(c)
    return asyncio.run(go())


def audit_rows(tmp):
    path = os.path.join(tmp, ".local/state/lumen-mcp-guard/t.jsonl")
    with open(path) as fh:
        return [json.loads(line) for line in fh]


def test_hung_call_times_out_fast_with_jsonrpc_32001_and_session_survives(env):
    from mcp.shared.exceptions import McpError

    async def go(c):
        t0 = time.monotonic()
        with pytest.raises(McpError) as ei:
            await c.call_tool("slow", {"seconds": 120})
        elapsed = time.monotonic() - t0
        after = await c.call_tool("fast", {"x": "still-here"})  # same backend session
        return ei.value, elapsed, after.content[0].text
    err, elapsed, after = run(env, cmd(env[1], extra=["--call-timeout", "2"]), go)
    assert err.error.code == -32001
    assert "timed out after 2s" in err.error.message
    assert elapsed < 10, elapsed  # bounded by the guard, not the 60 s client timeout
    assert after == "fast:still-here"
    rows = [r for r in audit_rows(env[1]) if r["decision"] == "TIMEOUT"]
    assert len(rows) == 1 and rows[0]["tool"] == "slow" and rows[0]["arg_keys"] == ["seconds"]
    assert "120" not in rows[0]["reason"]  # keys only, never values
    start = [r for r in audit_rows(env[1]) if r["decision"] == "START"][0]
    assert "call_timeout=2s" in start["reason"]


def test_call_inside_the_deadline_is_untouched(env):
    async def go(c):
        return (await c.call_tool("slow", {"seconds": 0.2})).content[0].text
    assert run(env, cmd(env[1], extra=["--call-timeout", "5"]), go) == "slow-done"
    assert not [r for r in audit_rows(env[1]) if r["decision"] == "TIMEOUT"]


def test_hung_tools_list_times_out_too(env):
    from mcp.shared.exceptions import McpError

    async def go(c):
        t0 = time.monotonic()
        with pytest.raises(McpError) as ei:
            await c.list_tools()
        return ei.value, time.monotonic() - t0
    err, elapsed = run(env, cmd(env[1], backend="mute.py", allow="anything", extra=["--call-timeout", "2"]), go)
    assert err.error.code == -32001 and "tools/list" in err.error.message
    assert elapsed < 10, elapsed


def test_default_is_30s_and_zero_turns_it_off(env):
    async def go(c):
        return (await c.call_tool("fast", {"x": "1"})).content[0].text
    run(env, cmd(env[1]), go)
    assert "call_timeout=30s" in [r for r in audit_rows(env[1]) if r["decision"] == "START"][-1]["reason"]
    run(env, cmd(env[1], extra=["--call-timeout", "0"]), go)
    assert "call_timeout=off" in [r for r in audit_rows(env[1]) if r["decision"] == "START"][-1]["reason"]


def test_zero_means_no_deadline_so_the_client_timeout_is_what_fires(env):
    """With the guard deadline off, nothing in the guard bounds the call (the pre-wq#5167 shape)."""
    from mcp.shared.exceptions import McpError

    async def go(c):
        with pytest.raises(McpError) as ei:
            await c.call_tool("slow", {"seconds": 30})
        return ei.value
    err = run(env, cmd(env[1], extra=["--call-timeout", "0"]), go, client_timeout=3)
    assert "lumen-mcp-guard" not in str(err.error.message)
    assert not [r for r in audit_rows(env[1]) if r["decision"] == "TIMEOUT"]


@pytest.mark.parametrize("bad", ["-1", "nan", "x"])
def test_bad_call_timeout_is_a_config_error(env, bad):
    import subprocess
    p = subprocess.run(cmd(env[1], extra=["--call-timeout", bad]), capture_output=True, text=True,
                       env=env[0], timeout=30, stdin=subprocess.DEVNULL)
    assert p.returncode != 0
