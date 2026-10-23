#!/usr/bin/env python3
"""mcp_smoke: speak MCP over stdio to a server command, list its tools, optionally call some.

  mcp_smoke.py [--call TOOL --args JSON]... [--max-chars N] -- SERVER_CMD ARGS...

Inherits the caller's environment, so a launcher that reads ~/.secrets behaves the way it does
under Claude Code. Output is truncated per call; tool results are printed, so do not point it
at a tool that returns secrets. Exit 0 if every call succeeded, 1 if a call returned an error,
2 if the server would not start.
"""
import argparse
import asyncio
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")


async def run(cmd, calls, max_chars):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    rc = 0
    async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=dict(os.environ)), timeout=120) as client:
        tools = await client.list_tools()
        print(f"tools/list: {len(tools)} tools")
        print("  " + ", ".join(sorted(t.name for t in tools)))
        for tool, raw in calls:
            try:
                res = await client.call_tool(tool, json.loads(raw), raise_on_error=False)
            except Exception as exc:
                print(f"call {tool}: EXCEPTION {type(exc).__name__}: {str(exc)[:400]}")
                rc = 1
                continue
            text = " ".join(getattr(c, "text", "") or "" for c in res.content)
            flag = "ERROR" if res.is_error else "ok"
            print(f"call {tool}({raw}): {flag}: {text[:max_chars]}")
            rc = rc or (1 if res.is_error else 0)
    return rc


def main(argv):
    if "--" not in argv:
        raise SystemExit("server command missing: put it after --")
    i = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", action="append", default=[])
    ap.add_argument("--args", action="append", default=[])
    ap.add_argument("--max-chars", type=int, default=600)
    a = ap.parse_args(argv[:i])
    if len(a.call) != len(a.args):
        raise SystemExit("each --call needs a matching --args JSON")
    try:
        return asyncio.run(run(argv[i + 1:], list(zip(a.call, a.args)), a.max_chars))
    except Exception as exc:
        print(f"ERR server did not start or answer: {type(exc).__name__}: {str(exc)[:400]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
