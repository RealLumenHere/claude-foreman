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
  * every decision is appended to ~/.local/state/lumen-mcp-guard/<name>.jsonl. Tool names and
    argument KEYS only, never values: values can carry typed secrets.

Usage:
  lumen_mcp_guard.py --name N --allow a,b,c [--http-url-arg tool:arg] [--arg-allow tool:arg=v1|v2]
                     [--mask-output CMD] [--egress-proxy]
                     [--env K=V] -- BACKEND_CMD ARGS...
  lumen_mcp_guard.py --list-backend --allow a,b,c -- BACKEND_CMD ARGS...
      prints the backend's tools split into exposed/hidden/missing; exit 1 if an allowlisted
      tool no longer exists upstream (drift or typo), exit 2 if the backend cannot be queried.
"""
import argparse
import asyncio
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


def make_policy(allow, url_args, arg_allow, audit, mask_cmd=None):
    from fastmcp.exceptions import ToolError
    from fastmcp.server.middleware import Middleware

    class Policy(Middleware):
        async def on_list_tools(self, context, call_next):
            tools = await call_next(context)
            return [t for t in tools if t.name in allow]

        async def on_call_tool(self, context, call_next):
            name = context.message.name
            arguments = context.message.arguments or {}
            if name not in allow:
                audit(name, "DENY", "not on allowlist", arguments)
                raise ToolError(f"tool {name!r} is not permitted by the lumen MCP guard")
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


async def list_backend(cmd, env, allow):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=env)) as client:
        names = sorted(t.name for t in await client.list_tools())
    report = {"backend_tools": names, "exposed": sorted(set(names) & allow),
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
    ap.add_argument("--list-backend", action="store_true")
    args = ap.parse_args(head)
    allow = {t.strip() for t in args.allow.split(",") if t.strip()}
    if not allow or not cmd:
        raise SystemExit("refusing to start: empty allowlist or backend command")
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
        async with backend:
            server = FastMCP.as_proxy(backend, name=args.name)
            server.add_middleware(make_policy(allow, url_args, arg_allow, make_audit(args.name), mask_cmd))
            await server.run_async(transport="stdio", show_banner=False)

    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
