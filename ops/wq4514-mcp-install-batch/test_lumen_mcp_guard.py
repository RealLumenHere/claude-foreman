"""wq#4514-4518: the guard must fail closed. Runs the real guard as a subprocess in front of a dummy backend."""
import asyncio
import json
import os
import subprocess
import sys
import textwrap
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "lumen_mcp_guard.py")
SIBLING = os.path.join(HERE, "..", "wq4206-stealth-browser-mcp")

BACKEND = textwrap.dedent('''
    import sys, warnings
    warnings.filterwarnings("ignore")
    from fastmcp import FastMCP
    import mcp.server.session as _ss
    import mcp.types as _types
    INITS = {"n": 0}
    _orig = _ss.ServerSession._received_request
    async def _counting(self, responder):
        if isinstance(responder.request.root, _types.InitializeRequest):
            INITS["n"] += 1
        return await _orig(self, responder)
    _ss.ServerSession._received_request = _counting
    m = FastMCP("dummy")
    @m.tool
    def safe_read(x: str) -> str:
        return "read:" + x
    @m.tool
    def danger_delete(x: str) -> str:
        return "deleted:" + x
    @m.tool
    def open_url(url: str) -> str:
        return "opened:" + url
    @m.tool
    def leak() -> str:
        return "line1 token=SECRET123 line2"
    @m.tool
    def init_count() -> str:
        return str(INITS["n"])
    @m.tool
    def pr_write(method: str) -> str:
        return "pr:" + method
    @m.tool
    def load(file: str = "") -> str:
        return "loaded:" + file
    @m.tool
    def argv() -> str:
        return " ".join(sys.argv[1:])
    @m.resource("res://secret")
    def res() -> str:
        return "resource-body"
    @m.prompt
    def prm() -> str:
        return "prompt-body"
    m.run(transport="stdio", show_banner=False)
''')


@pytest.fixture()
def env(tmp_path):
    (tmp_path / "backend.py").write_text(BACKEND)
    e = dict(os.environ)
    e["HOME"] = str(tmp_path)  # isolates the audit log
    e["PYTHONPATH"] = os.pathsep.join([SIBLING, e.get("PYTHONPATH", "")])
    return e, str(tmp_path)


def guard_cmd(tmp, extra=None, allow="safe_read,open_url,argv"):
    return [sys.executable, GUARD, "--name", "t", "--allow", allow, *(extra or []),
            "--", sys.executable, os.path.join(tmp, "backend.py")]


def with_client(env_tmp, cmd, coro):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    e, _ = env_tmp

    async def go():
        async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=e), timeout=60) as c:
            return await coro(c)
    return asyncio.run(go())


def test_only_allowlisted_tools_are_listed(env):
    names = with_client(env, guard_cmd(env[1]), lambda c: c.list_tools())
    assert sorted(t.name for t in names) == ["argv", "open_url", "safe_read"]  # pr_write/danger_delete hidden


def test_backend_sees_exactly_one_initialize(env):
    """Go/Node MCP servers reject a second initialize on the same process; a per-request proxy session sends one."""
    async def go(c):
        await c.list_tools()
        await c.call_tool("safe_read", {"x": "1"})
        await c.call_tool("safe_read", {"x": "2"})
        await c.list_tools()
        return (await c.call_tool("init_count", {})).content[0].text
    assert with_client(env, guard_cmd(env[1], allow="safe_read,init_count"), go) == "1"


MASKER = "import sys; sys.stdout.write(sys.stdin.read().replace('SECRET123', '<MASKED>'))"


def test_output_masking_redacts_before_the_agent_sees_it(env):
    cmd = guard_cmd(env[1], ["--mask-output", f"{sys.executable} -c \"{MASKER}\""], allow="leak")

    async def go(c):
        return await c.call_tool("leak", {})
    res = with_client(env, cmd, go)
    body = json.dumps([getattr(c, "text", "") for c in res.content]) + json.dumps(res.structured_content)
    assert "SECRET123" not in body and "<MASKED>" in body


def test_output_masking_fails_closed_when_the_masker_breaks(env):
    cmd = guard_cmd(env[1], ["--mask-output", "/nonexistent/masker"], allow="leak")

    async def go(c):
        return await c.call_tool("leak", {}, raise_on_error=False)
    res = with_client(env, cmd, go)
    body = json.dumps([getattr(c, "text", "") for c in res.content]) + json.dumps(res.structured_content)
    assert res.is_error and "SECRET123" not in body and "withheld" in body


def test_output_masking_fails_closed_when_the_masker_exits_nonzero(env):
    cmd = guard_cmd(env[1], ["--mask-output", f"{sys.executable} -c \"import sys; sys.exit(3)\""], allow="leak")

    async def go(c):
        return await c.call_tool("leak", {}, raise_on_error=False)
    res = with_client(env, cmd, go)
    body = json.dumps([getattr(c, "text", "") for c in res.content])
    assert res.is_error and "SECRET123" not in body


def test_denied_tool_cannot_be_called_even_by_name(env):
    """wq#4947: hidden, nonexistent and empty names all get the same JSON-RPC -32602, not an isError result."""
    from mcp.shared.exceptions import McpError

    async def go(c):
        out = {}
        for name in ("danger_delete", "no_such_tool", ""):
            try:
                r = await c.call_tool(name, {"x": "1"}, raise_on_error=False)
                out[name] = ("result", r.is_error)
            except McpError as exc:
                out[name] = (exc.error.code, exc.error.message.split(":")[0])
        out["after"] = (await c.call_tool("init_count", {})).content[0].text  # session survives
        return out
    out = with_client(env, guard_cmd(env[1], allow="safe_read,init_count"), go)
    for name in ("danger_delete", "no_such_tool", ""):
        assert out[name] == (-32602, "Unknown tool"), (name, out[name])
    assert out["after"] == "1"


def test_undeclared_and_mistyped_arguments_are_refused_before_the_backend(env):
    """wq#4947 F7: docker list_volumes took 24/24 malformed inputs. Extras, wrong types, missing required."""
    async def go(c):
        out = {}
        cases = {"ok": {"x": "1"}, "extra": {"x": "1", "all": True}, "only_extra": {"y": "1"},
                 "type": {"x": 5}, "missing": {}, "nested": {"x": {"a": 1}}}
        for label, a in cases.items():
            r = await c.call_tool("safe_read", a, raise_on_error=False)
            out[label] = (r.is_error, getattr(r.content[0], "text", "") if r.content else "")
        r = await c.call_tool("leak", {"surprise": 1}, raise_on_error=False)  # no-arg tool
        out["noarg_extra"] = (r.is_error, r.content[0].text)
        return out
    out = with_client(env, guard_cmd(env[1], allow="safe_read,leak"), go)
    assert out["ok"] == (False, "read:1")
    for label in ("extra", "only_extra", "type", "missing", "nested", "noarg_extra"):
        assert out[label][0] is True and not out[label][1].startswith("read:"), (label, out[label])
    assert "invalid arguments" in out["extra"][1] and "'all'" in out["extra"][1]
    log = os.path.join(env[1], ".local/state/lumen-mcp-guard/t.jsonl")
    rows = [json.loads(l) for l in open(log)]
    denied = [r for r in rows if r["decision"] == "DENY"]
    assert any(r["reason"] == "schema: additionalProperties at <root>" for r in denied)


def test_check_schema_rules():
    sys.path.insert(0, HERE)
    import lumen_mcp_guard as g
    s = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    assert g.check_schema(s, {"x": "a"}) is None
    assert g.check_schema(s, {"x": "a", "y": 1})[1] == "schema: additionalProperties at <root>"
    assert g.check_schema(dict(s, additionalProperties=True), {"x": "a", "y": 1}) is None  # schema opts in
    assert g.check_schema(dict(s, additionalProperties=False), {"x": "a", "y": 1}) is not None
    assert g.check_schema({"type": 7}, {})[1] == "schema: backend schema invalid"  # fail closed
    assert g.check_schema(None, {}) is None and g.check_schema(None, {"y": 1}) is not None
    detail, reason = g.check_schema({"type": "object", "properties": {"n": {"type": "integer"}}}, {"n": "TOPSECRET"})
    assert "TOPSECRET" in detail and "TOPSECRET" not in reason and reason == "schema: type at n"  # audit is value-free


def raw_initialize(env_tmp, params):
    """Send one raw initialize line to the guard and return the parsed reply."""
    e, tmp = env_tmp
    p = subprocess.Popen(guard_cmd(tmp), env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    try:
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())
    finally:
        p.kill()
        p.wait(timeout=30)


def test_initialize_with_empty_protocol_version_or_client_name_is_a_jsonrpc_error(env):
    good = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}
    assert raw_initialize(env, good)["result"]["protocolVersion"] == "2025-06-18"
    for bad in (dict(good, protocolVersion=""), dict(good, protocolVersion="  "),
                dict(good, clientInfo={"name": "", "version": "1"})):
        reply = raw_initialize(env, bad)
        assert "result" not in reply and reply["error"]["code"] == -32602, (bad, reply)


def test_allowed_tool_passes_through(env):
    async def go(c):
        return await c.call_tool("safe_read", {"x": "hi"})
    assert with_client(env, guard_cmd(env[1]), go).content[0].text == "read:hi"


def test_url_argument_policy(env):
    cmd = guard_cmd(env[1], ["--http-url-arg", "open_url:url"])

    async def go(c):
        out = {}
        for u in ("https://example.com/x", "http://example.org", "file:///etc/passwd",
                  "javascript:alert(1)", "data:text/html,hi", "about:blank", "chrome://settings"):
            r = await c.call_tool("open_url", {"url": u}, raise_on_error=False)
            out[u] = r.is_error
        return out
    out = with_client(env, cmd, go)
    assert out["https://example.com/x"] is False and out["http://example.org"] is False
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi", "about:blank", "chrome://settings"):
        assert out[bad] is True, bad


def test_arg_allow_closes_a_multiplexed_tool_to_reviewed_values(env):
    cmd = guard_cmd(env[1], ["--arg-allow", "pr_write:method=create|update|close"], allow="safe_read,pr_write")

    async def go(c):
        out = {}
        for m in ("create", "update", "close", "merge", "Merge", "create ", ""):
            r = await c.call_tool("pr_write", {"method": m}, raise_on_error=False)
            out[m] = r.is_error
        r = await c.call_tool("pr_write", {}, raise_on_error=False)
        out["<absent>"] = r.is_error
        return out
    out = with_client(env, cmd, go)
    assert [out[m] for m in ("create", "update", "close")] == [False, False, False]
    for bad in ("merge", "Merge", "create ", "", "<absent>"):
        assert out[bad] is True, bad


def test_arg_allow_malformed_spec_is_a_config_error(env):
    for spec in ("pr_write", "pr_write:method", "pr_write:method=", ":method=a", "pr_write:=a"):
        p = subprocess.run(guard_cmd(env[1], ["--arg-allow", spec], allow="pr_write"), env=env[0],
                           capture_output=True, text=True, timeout=30)
        assert p.returncode != 0 and "--arg-allow wants" in p.stderr, spec


def test_path_arg_pins_a_host_path_under_its_root(env):
    """wq#4946: ghidra-lumen load_program/import_file read any host file the server user can."""
    tmp = env[1]
    root = os.path.join(tmp, "re")
    os.makedirs(os.path.join(root, "bins"))
    os.makedirs(os.path.join(tmp, "re-evil"))
    open(os.path.join(root, "bins", "a.bin"), "wb").close()
    os.symlink("/etc/passwd", os.path.join(root, "bins", "escape"))
    cmd = guard_cmd(tmp, ["--path-arg", f"load:file={root}"], allow="load")
    probes = {
        "inside": os.path.join(root, "bins", "a.bin"),
        "root": root,
        "outside": "/etc/passwd",
        "dotdot": os.path.join(root, "..", "re-evil", "x"),
        "sibling_prefix": os.path.join(tmp, "re-evil", "x"),
        "symlink_escape": os.path.join(root, "bins", "escape"),
        "relative": "bins/a.bin",
        "empty": "",
    }

    async def go(c):
        out = {k: (await c.call_tool("load", {"file": v}, raise_on_error=False)).is_error for k, v in probes.items()}
        out["absent"] = (await c.call_tool("load", {}, raise_on_error=False)).is_error
        return out
    out = with_client(env, cmd, go)
    assert out["inside"] is False and out["root"] is False
    for bad in ("outside", "dotdot", "sibling_prefix", "symlink_escape", "relative", "empty", "absent"):
        assert out[bad] is True, bad


def test_path_arg_malformed_spec_is_a_config_error(env):
    for spec in ("load", "load:file", "load:file=", "load:file=relative/root", ":file=/x"):
        p = subprocess.run(guard_cmd(env[1], ["--path-arg", spec], allow="load"), env=env[0],
                           capture_output=True, text=True, timeout=30)
        assert p.returncode != 0 and "--path-arg wants" in p.stderr, spec
    p = subprocess.run(guard_cmd(env[1], ["--path-arg", "load:file=/x"], allow="safe_read"), env=env[0],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode != 0 and "not on the allowlist" in p.stderr


def test_resources_and_prompts_are_hidden(env):
    async def go(c):
        res = await c.list_resources()
        prm = await c.list_prompts()
        try:
            await c.read_resource("res://secret")
            read_ok = True
        except Exception:
            read_ok = False
        return res, prm, read_ok
    res, prm, read_ok = with_client(env, guard_cmd(env[1]), go)
    assert res == [] and prm == [] and read_ok is False


def test_audit_log_has_tool_names_and_arg_keys_but_never_values(env):
    async def go(c):
        await c.call_tool("safe_read", {"x": "TOPSECRET-VALUE-123"})
        with pytest.raises(Exception, match="Unknown tool"):
            await c.call_tool("danger_delete", {"x": "TOPSECRET-VALUE-123"})
    with_client(env, guard_cmd(env[1]), go)
    log = os.path.join(env[1], ".local/state/lumen-mcp-guard/t.jsonl")
    body = open(log).read()
    rows = [json.loads(l) for l in body.splitlines()]
    assert {(r["tool"], r["decision"]) for r in rows} == {("safe_read", "ALLOW"), ("danger_delete", "DENY")}
    assert "TOPSECRET" not in body and rows[0]["arg_keys"] == ["x"]


def test_empty_allowlist_refuses_to_start(env):
    p = subprocess.run([sys.executable, GUARD, "--allow", " , ", "--", sys.executable, "-c", "pass"],
                       env=env[0], capture_output=True, text=True)
    assert p.returncode != 0 and "refusing to start" in (p.stderr + p.stdout)


def test_url_arg_on_unlisted_tool_is_a_config_error(env):
    p = subprocess.run(guard_cmd(env[1], ["--http-url-arg", "danger_delete:x"]), env=env[0],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode != 0 and "not on the allowlist" in p.stderr


def test_list_backend_reports_drift(env):
    ok = subprocess.run(guard_cmd(env[1], ["--list-backend"]), env=env[0], capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0
    rep = json.loads(ok.stdout)
    assert rep["hidden"] == ["danger_delete", "init_count", "leak", "load", "pr_write"] and rep["missing_upstream"] == []
    bad = subprocess.run(guard_cmd(env[1], ["--list-backend"], allow="safe_read,renamed_upstream"), env=env[0],
                         capture_output=True, text=True, timeout=60)
    assert bad.returncode == 1 and json.loads(bad.stdout)["missing_upstream"] == ["renamed_upstream"]


def test_list_backend_unqueryable_is_exit_2(env):
    p = subprocess.run([sys.executable, GUARD, "--list-backend", "--allow", "a", "--", "/nonexistent/binary"],
                       env=env[0], capture_output=True, text=True, timeout=60)
    assert p.returncode == 2


@pytest.mark.skipif(not os.path.exists(os.path.join(SIBLING, "lumen_egress_proxy.py")), reason="wq4206 bundle absent")
def test_egress_proxy_placeholder_is_substituted(env):
    cmd = guard_cmd(env[1], ["--egress-proxy"])
    cmd = cmd + ["--proxy-server", "{EGRESS_PROXY}"]

    async def go(c):
        return (await c.call_tool("argv", {})).content[0].text
    out = with_client(env, cmd, go)
    assert out.startswith("--proxy-server http://127.0.0.1:") and "{EGRESS_PROXY}" not in out
