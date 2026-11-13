#!/usr/bin/env python3
"""lumen_mcp_guard: run a third-party stdio MCP server behind a fail-closed tool allowlist.

wq#4516 (docker) and wq#4517 (playwright) install servers whose tools reach root-equivalent
power or arbitrary network egress. wq#4290 showed what an unguarded server of that kind does
(stealth-browser execute_script SSRF into loopback/tailnet). This launcher sits between Claude
Code and the real server:

  * only tools named by --allow are listed or callable. Tools a later upstream release adds
    stay hidden until someone reviews them and extends the allowlist (fail closed);
  * resources, resource templates and prompts are never exposed;
  * --http-url-arg TOOL:ARG pins an argument of a tool to http(s) URLs with a host;
  * --arg-allow TOOL:ARG=v1|v2 pins an argument to a closed set of values. An absent
    argument or any other value is refused. This is how a multiplexed tool such as
    pull_request_write(method=...) is cut down to the methods someone reviewed;
  * --mask-output CMD pipes every text result through CMD (the fleet's mask-secrets) before it
    reaches the agent. Fail closed: if CMD errors, the result is withheld, not passed raw;
  * --egress-proxy starts lumen_egress_proxy (public hosts only, resolved and IP-pinned per
    connection) and substitutes its URL for {EGRESS_PROXY} in the backend command;
  * --pins FILE pins every exposed tool to a sha256 over its definition (name, title,
    description, inputSchema, outputSchema, annotations; the canonical form of the
    security-gate snapshotter, so its baseline file is used as is). An allowlisted tool whose
    definition no longer matches its pin, or has no pin, is hidden from tools/list and refused
    on tools/call, with a DRIFT audit row (wq#4941). The backend is re-listed on every request,
    so this is what stops a mid-session description rug-pull or schema change. Pins are read
    from surface --pins-surface (default NAME@guarded: the definition as the agent sees it);
  * every decision is appended to ~/.local/state/lumen-mcp-guard/<name>.jsonl. Tool names and
    argument KEYS only, never values: values can carry typed secrets. The log also gets a START
    row with the backend's own serverInfo (the agent only ever sees the guard's), a LIST row
    when the backend's tool names change, and a CHANGED row when an exposed tool's definition
    changes while no pins are loaded.

Protocol hardening (wq#4947, audit F7), following the MCP 2025-11-25 tools spec split:
  * a tools/call naming a tool that is not on the allowlist (hidden, nonexistent or '') is a
    JSON-RPC error -32602 "Unknown tool", the same answer for all three;
  * arguments are validated against the backend's inputSchema with undeclared keys refused
    (additionalProperties defaults to false unless the schema opts in). That is an input
    validation error, so it comes back as an isError result the model can correct;
  * initialize with an empty protocolVersion or clientInfo.name is a JSON-RPC error -32602.

Usage:
  lumen_mcp_guard.py --name N --allow a,b,c [--http-url-arg tool:arg] [--arg-allow tool:arg=v1|v2]
                     [--mask-output CMD] [--egress-proxy] [--pins FILE [--pins-surface KEY]]
                     [--env K=V] -- BACKEND_CMD ARGS...
  lumen_mcp_guard.py --list-backend --allow a,b,c -- BACKEND_CMD ARGS...
      prints the backend's serverInfo and tools split into exposed/hidden/missing; exit 1 if an allowlisted
      tool no longer exists upstream (drift or typo), exit 2 if the backend cannot be queried.
"""
import argparse
import asyncio
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
import warnings
from urllib.parse import urlsplit

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I drops the script dir

STATE_DIR = os.path.expanduser("~/.local/state/lumen-mcp-guard")


class PolicyError(ValueError):
    pass


def check_http_url(value):
    parts = urlsplit(str(value).strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise PolicyError("only absolute http(s) URLs with a host are allowed")


def parse_url_args(specs):
    out = {}
    for spec in specs:
        tool, sep, arg = spec.partition(":")
        if not sep or not tool or not arg:
            raise SystemExit(f"--http-url-arg wants TOOL:ARG, got {spec!r}")
        out.setdefault(tool, set()).add(arg)
    return out


def parse_arg_allow(specs):
    out = {}
    for spec in specs:
        left, eq, values = spec.partition("=")
        tool, colon, arg = left.partition(":")
        vals = {v for v in values.split("|") if v}
        if not (eq and colon and tool and arg and vals):
            raise SystemExit(f"--arg-allow wants TOOL:ARG=v1|v2, got {spec!r}")
        out.setdefault(tool, {})[arg] = vals
    return out


def mask_text(cmd, text):
    try:
        p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PolicyError(f"masker could not run: {type(exc).__name__}") from None
    if p.returncode != 0:
        raise PolicyError(f"masker exited {p.returncode}")
    return p.stdout


def mask_result(cmd, result):
    for item in getattr(result, "content", None) or []:
        if getattr(item, "text", None):
            item.text = mask_text(cmd, item.text)
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        try:
            result.structured_content = json.loads(mask_text(cmd, json.dumps(sc)))
        except ValueError:
            result.structured_content = None  # masking broke the JSON; the masked text content remains
    return result


def check_schema(schema, arguments):
    """Validate arguments against a backend inputSchema, refusing keys the schema does not declare.

    Returns None when valid, else (detail for the caller, value-free reason for the audit log).
    """
    import jsonschema
    strict = dict(schema or {})
    strict.setdefault("additionalProperties", False)
    try:
        cls = jsonschema.validators.validator_for(strict)
        cls.check_schema(strict)
        err = jsonschema.exceptions.best_match(cls(strict).iter_errors(arguments))
    except jsonschema.exceptions.SchemaError:
        return "backend inputSchema is not a valid JSON Schema", "schema: backend schema invalid"
    if err is None:
        return None
    where = "/".join(str(p) for p in err.absolute_path) or "<root>"
    return f"{where}: {err.message[:200]}", f"schema: {err.validator} at {where}"


def tool_sha256(mcp_tool):
    """sha256 of an mcp.types.Tool, byte-compatible with the security-gate snap.py baseline."""
    ann = getattr(mcp_tool, "annotations", None)
    d = {"name": mcp_tool.name, "title": getattr(mcp_tool, "title", None),
         "description": mcp_tool.description or "", "inputSchema": mcp_tool.inputSchema,
         "outputSchema": getattr(mcp_tool, "outputSchema", None),
         "annotations": ann.model_dump(exclude_none=True) if ann else None}
    canon = json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


def load_pins(path, surface):
    try:
        with open(path) as fh:
            doc = json.load(fh)
        tools = doc["servers"][surface]["tools"]
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--pins: cannot read {path}: {type(exc).__name__}: {exc}")
    except (KeyError, TypeError):
        raise SystemExit(f"--pins: {path} has no servers[{surface!r}].tools")
    if not isinstance(tools, dict) or not all(isinstance(v, str) for v in tools.values()):
        raise SystemExit(f"--pins: servers[{surface!r}].tools must map tool name -> sha256")
    return tools


class DefinitionWatch:
    """Tracks what the backend advertises across re-lists and judges each exposed tool against its pin.

    Audit rows are written on change only: the proxy re-lists the backend on every request, and a
    row per list would bury the one that matters."""

    def __init__(self, pins, audit):
        self.pins, self.audit = pins, audit
        self.names = None
        self.seen = {}

    def note_names(self, names):
        if self.names is not None and names != self.names:
            added, removed = sorted(names - self.names), sorted(self.names - names)
            self.audit("-", "LIST", f"backend tools added={added} removed={removed}", {})
        self.names = names

    def admits(self, tool):
        """True if this definition of an allowlisted tool may reach the agent."""
        name, digest = tool.name, tool_sha256(tool.to_mcp_tool())
        prev = self.seen.get(name)
        self.seen[name] = digest
        if self.pins is None:
            if prev is not None and prev != digest:
                self.audit(name, "CHANGED", f"definition sha256 {prev[:12]} -> {digest[:12]} (no pins loaded)", {})
            return True
        pin = self.pins.get(name)
        if digest == pin:
            if prev is not None and prev != digest:
                self.audit(name, "PIN_OK", f"definition matches pin {pin[:12]} again; tool exposed", {})
            return True
        if digest != prev:
            why = f"sha256 {digest[:12]} != pin {pin[:12]}" if pin else f"sha256 {digest[:12]}, no pin"
            self.audit(name, "DRIFT", f"definition {why}; tool hidden", {})
        return False


def make_policy(allow, url_args, arg_allow, audit, lookup, mask_cmd=None, pins=None):
    from fastmcp.exceptions import ToolError
    from fastmcp.server.middleware import Middleware

    watch = DefinitionWatch(pins, audit)

    class Policy(Middleware):
        async def on_list_tools(self, context, call_next):
            tools = await call_next(context)
            watch.note_names({t.name for t in tools})
            return [t for t in tools if t.name in allow and watch.admits(t)]

        async def on_call_tool(self, context, call_next):
            name = context.message.name
            arguments = context.message.arguments or {}
            if name not in allow:  # gate_unknown_tools answers first; this is the backstop
                audit(name, "DENY", "not on allowlist", arguments)
                raise ToolError(f"tool {name!r} is not permitted by the lumen MCP guard")
            try:
                current = await lookup(name)
            except Exception as exc:  # backend cannot describe the tool: do not forward blind
                audit(name, "DENY", f"schema unavailable: {type(exc).__name__}", arguments)
                raise ToolError(f"{name}: input schema unavailable, call refused") from None
            # The agent may call a tool it listed before the swap, or never list at all:
            # judge the definition the backend offers now, not the last one we showed.
            if pins is not None and not watch.admits(current):
                audit(name, "DENY", "definition drifted from pin", arguments)
                raise ToolError(f"tool {name!r} is withheld: its definition does not match the reviewed pin")
            bad = check_schema(current.parameters, arguments)
            if bad:
                audit(name, "DENY", bad[1], arguments)
                raise ToolError(f"{name}: invalid arguments: {bad[0]}")
            for arg in sorted(url_args.get(name, ())):
                if arg in arguments and arguments[arg] is not None:
                    try:
                        check_http_url(arguments[arg])
                    except PolicyError as exc:
                        audit(name, "DENY", f"{arg}: {exc}", arguments)
                        raise ToolError(f"{name}: argument {arg!r}: {exc}") from None
            for arg, allowed in sorted(arg_allow.get(name, {}).items()):
                value = arguments.get(arg)
                if value is None or str(value) not in allowed:
                    audit(name, "DENY", f"{arg}: value not in reviewed set", arguments)
                    raise ToolError(f"{name}: {arg}={str(value)[:40]!r} is not permitted; allowed: {sorted(allowed)}")
            audit(name, "ALLOW", "", arguments)
            result = await call_next(context)
            if mask_cmd:
                try:
                    result = mask_result(mask_cmd, result)
                except PolicyError as exc:
                    audit(name, "DENY", f"output withheld: {exc}", arguments)
                    raise ToolError(f"{name}: output withheld, masking failed ({exc})") from None
            return result

        async def on_list_resources(self, context, call_next):
            return []

        async def on_list_resource_templates(self, context, call_next):
            return []

        async def on_list_prompts(self, context, call_next):
            return []

        async def on_read_resource(self, context, call_next):
            raise ToolError("resources are not exposed by the lumen MCP guard")

        async def on_get_prompt(self, context, call_next):
            raise ToolError("prompts are not exposed by the lumen MCP guard")

    return Policy()


def gate_unknown_tools(lowlevel, allow, audit):
    """Answer tools/call for a tool outside the allowlist with JSON-RPC -32602, not an isError result.

    Anything raised under mcp's call_tool handler becomes isError, so the check wraps the
    registered request handler; McpError from there is sent as a JSON-RPC error.
    """
    import mcp.types as mt
    from mcp.shared.exceptions import McpError
    inner = lowlevel.request_handlers[mt.CallToolRequest]

    async def handler(req):
        name = req.params.name
        if name not in allow:
            audit(name, "DENY", "not on allowlist", req.params.arguments or {})
            raise McpError(mt.ErrorData(code=mt.INVALID_PARAMS, message=f"Unknown tool: {name[:80]!r}"))
        return await inner(req)
    lowlevel.request_handlers[mt.CallToolRequest] = handler


def harden_initialize():
    """Refuse initialize with an empty protocolVersion or clientInfo.name (mcp accepts both)."""
    import mcp.types as mt
    from mcp.server.session import ServerSession
    orig = ServerSession._received_request

    async def received(self, responder):
        req = responder.request.root
        if isinstance(req, mt.InitializeRequest) and not (
                req.params.protocolVersion.strip() and req.params.clientInfo.name.strip()):
            with responder:
                await responder.respond(mt.ErrorData(
                    code=mt.INVALID_PARAMS, message="initialize needs a non-empty protocolVersion and clientInfo.name"))
            return
        return await orig(self, responder)
    ServerSession._received_request = received


def make_audit(name):
    def audit(tool, decision, reason, arguments):
        try:
            os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "server": name,
                   "tool": tool, "decision": decision, "reason": reason,
                   "arg_keys": sorted(arguments)}
            with open(os.path.join(STATE_DIR, f"{name}.jsonl"), "a") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            pass  # auditing must never take the tool surface down
    return audit


def split_command(argv):
    if "--" not in argv:
        raise SystemExit("backend command missing: put it after --")
    i = argv.index("--")
    return argv[:i], argv[i + 1:]


def build_env(pairs):
    env = dict(os.environ)
    for pair in pairs:
        key, sep, val = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--env wants K=V, got {pair!r}")
        env[key] = val
    return env


def server_info(client):
    """The backend's own serverInfo, e.g. {'name': 'Gitea MCP Server', 'version': '1.8.1'}."""
    try:
        si = client.initialize_result.serverInfo
        return {"name": si.name, "version": si.version}
    except Exception:  # not initialized or a non-conforming backend: say so, do not fail the start
        return {"name": None, "version": None}


async def list_backend(cmd, env, allow):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=env)) as client:
        names = sorted(t.name for t in await client.list_tools())
        info = server_info(client)
    report = {"server_info": info, "backend_tools": names, "exposed": sorted(set(names) & allow),
              "hidden": sorted(set(names) - allow), "missing_upstream": sorted(allow - set(names))}
    print(json.dumps(report, indent=1))
    return 1 if report["missing_upstream"] else 0


def main(argv):
    head, cmd = split_command(argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="lumen-guard")
    ap.add_argument("--allow", required=True)
    ap.add_argument("--http-url-arg", action="append", default=[])
    ap.add_argument("--arg-allow", action="append", default=[])
    ap.add_argument("--mask-output", default=None)
    ap.add_argument("--egress-proxy", action="store_true")
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--pins", default=None)
    ap.add_argument("--pins-surface", default=None)
    ap.add_argument("--list-backend", action="store_true")
    args = ap.parse_args(head)
    allow = {t.strip() for t in args.allow.split(",") if t.strip()}
    if not allow or not cmd:
        raise SystemExit("refusing to start: empty allowlist or backend command")
    if args.pins_surface and not args.pins:
        raise SystemExit("--pins-surface needs --pins")
    pins = load_pins(args.pins, args.pins_surface or f"{args.name}@guarded") if args.pins else None
    if pins is not None and allow - set(pins):
        print(f"lumen_mcp_guard: allowlisted but unpinned, will stay hidden: {sorted(allow - set(pins))}",
              file=sys.stderr)
    url_args = parse_url_args(args.http_url_arg)
    arg_allow = parse_arg_allow(args.arg_allow)
    stray = (set(url_args) | set(arg_allow)) - allow
    if stray:
        raise SystemExit(f"--http-url-arg/--arg-allow name tools not on the allowlist: {sorted(stray)}")
    if args.egress_proxy:
        import lumen_egress_proxy
        proxy = lumen_egress_proxy.start()
        cmd = [c.replace("{EGRESS_PROXY}", proxy) for c in cmd]
    env = build_env(args.env)
    mask_cmd = shlex.split(args.mask_output) if args.mask_output else None
    if args.list_backend:
        try:
            return asyncio.run(list_backend(cmd, env, allow))
        except Exception as exc:  # backend would not start or answer
            print(f"ERR cannot query backend: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    from fastmcp import FastMCP
    from fastmcp.client.transports import StdioTransport
    from fastmcp.server.proxy import ProxyClient

    async def serve():
        backend = ProxyClient(StdioTransport(command=cmd[0], args=cmd[1:], env=env, keep_alive=True))
        # ONE backend session for the life of the guard. A proxy over a client that is not yet
        # connected opens a fresh MCP session per request, and servers that enforce the protocol
        # (gitea-mcp, Go) refuse the second `initialize` on the same process: tools/list works,
        # the first tools/call dies. Dummy Python backends tolerate it, which is how it got past
        # the first test run (test_backend_sees_exactly_one_initialize pins it).
        audit = make_audit(args.name)
        async with backend:
            audit("-", "START", f"backend serverInfo {server_info(backend)}; "
                  f"pins={'%d tools' % len(pins) if pins is not None else 'off'}", {})
            server = FastMCP.as_proxy(backend, name=args.name)
            server.add_middleware(make_policy(allow, url_args, arg_allow, audit, server.get_tool, mask_cmd, pins))
            gate_unknown_tools(server._mcp_server, allow, audit)
            await server.run_async(transport="stdio", show_banner=False)

    harden_initialize()
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
