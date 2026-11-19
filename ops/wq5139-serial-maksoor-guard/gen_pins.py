#!/usr/bin/env python3
"""wq#5139: print a --pins document for the tools a guarded launcher (started WITHOUT --pins) lists.

  gen_pins.py SURFACE -- LAUNCHER_CMD ARGS...

The hash is lumen_mcp_guard.tool_sha256, the same function the guard checks pins with, over the
definition as the agent sees it (after the guard's untrusted-output outputSchema strip and the
bridge's confirm_token schema addition). Review the definitions before committing the output:
a pin says "someone read this".
"""
import asyncio
import datetime
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/lumen/.local/share/mcp/_guard")


async def main(surface, cmd):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from lumen_mcp_guard import tool_sha256
    async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=dict(os.environ)), timeout=120) as c:
        tools = await c.list_tools()
    doc = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "note": "wq#5139 serial-maksoor guarded surface; regenerate with install.sh --gen-pins after review",
           "servers": {surface: {"tools": {t.name: tool_sha256(t) for t in sorted(tools, key=lambda t: t.name)}}}}
    print(json.dumps(doc, indent=1))


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 3 or a[1] != "--":
        raise SystemExit(__doc__)
    asyncio.run(main(a[0], a[2:]))
