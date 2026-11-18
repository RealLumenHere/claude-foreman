#!/usr/bin/env python3
"""Live smoke for mailbot-mcp through launcher.sh (the guard), using the mcp SDK client.

Calls only tools whose output is counts or not-found, so no mail body is printed.
Exit 0 when every check holds. --guard PATH runs the launcher against that guard copy.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

LAUNCHER = str(Path(__file__).resolve().parent / "launcher.sh")


def text(res):
    return " ".join(getattr(c, "text", "") or "" for c in res.content)


async def main(guard: str | None) -> int:
    ok = True
    env = dict(os.environ, **({"MAILBOT_MCP_GUARD": guard} if guard else {}))
    async with stdio_client(StdioServerParameters(command="bash", args=[LAUNCHER], env=env)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            print("tools:", ",".join(names))
            for tool, args, want_error in [
                ("mail_status", {}, False),
                ("mail_contact", {"who": "zz-nobody-zz"}, False),
                ("mail_pending_tasks", {}, False),
                ("mail_draft_create", {"task_id": "zz-no-task", "body": "x"}, True),
                ("mail_send", {}, True),
            ]:
                try:
                    res = await s.call_tool(tool, args)
                    err, out = res.isError, text(res)
                except Exception as exc:  # the guard answers unknown tools with a JSON-RPC error
                    err, out = True, f"{type(exc).__name__}: {exc}"
                good = err == want_error
                ok &= good
                print(f"{'PASS' if good else 'FAIL'} {tool}{json.dumps(args)} error={err}: {out[:300]}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--guard")
    sys.exit(asyncio.run(main(ap.parse_args().guard)))
