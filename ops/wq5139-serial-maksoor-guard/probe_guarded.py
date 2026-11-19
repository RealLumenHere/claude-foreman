#!/usr/bin/env python3
"""wq#5139 acceptance probe: drive the serial-maksoor launcher over stdio against the live serial-mcp.

  probe_guarded.py LAUNCHER [LAUNCHER ARGS...]

Touches no UART: only list/status reads plus calls that must be refused before they reach Maksoor.
Hidden tools are probed with arguments that would be harmless if the guard were missing (a profile
name that does not exist). Exit 0 when every expectation holds.
"""
import asyncio
import os
import sys
import warnings

warnings.filterwarnings("ignore")

GUARD_MARKS = ("not permitted by the lumen MCP guard", "Unknown tool:")
GATE_MARK = "needs a valid operator-issued confirm_token"
HIDDEN = ("configure", "save_profile", "delete_profile", "rollback_profile", "export_log")

PROBES = [  # (tool, args, expect)  expect: "guard" | "gate" | "ok"
    ("delete_profile", {"profile_name": "wq5139-probe-nonexistent"}, "guard"),
    ("export_log", {"connection_id": "wq5139-probe-nonexistent", "path": "x.jsonl"}, "guard"),
    ("configure", {"profile": "wq5139-probe-nonexistent", "defaults": {}}, "guard"),
    ("write", {"connection_id": "wq5139-probe-nonexistent", "data": "x"}, "gate"),
    ("transact", {"connection_id": "wq5139-probe-nonexistent", "data": "x"}, "gate"),
    ("set_dtr_rts", {"connection_id": "wq5139-probe-nonexistent", "dtr": False, "rts": False}, "gate"),
    ("send_break", {"connection_id": "wq5139-probe-nonexistent"}, "gate"),
    ("write", {"connection_id": "wq5139-probe-nonexistent", "data": "x", "confirm_token": "forged"}, "gate"),
    ("list_ports", {}, "ok"),
    ("list_profiles", {}, "ok"),
    ("list_connections", {}, "ok"),
    ("compute_checksum", {"algorithm": "xor", "data": "GPGGA", "encoding": "utf8"}, "ok"),
]


async def run(cmd):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    bad = 0
    async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=dict(os.environ)), timeout=120) as c:
        tools = {t.name: t for t in await c.list_tools()}
        print(f"tools/list: {len(tools)} tools")
        for h in HIDDEN:
            if h in tools:
                print(f"FAIL listed: {h}")
                bad += 1
        if "confirm_token" not in (tools.get("write").inputSchema or {}).get("properties", {}):
            print("FAIL write does not declare confirm_token")
            bad += 1
        for tool, args, expect in PROBES:
            try:
                r = await c.call_tool(tool, args, raise_on_error=False)
                is_error, text = r.is_error, " ".join(getattr(x, "text", "") or "" for x in r.content)
            except Exception as exc:
                is_error, text = True, str(exc)
            got = ("guard" if is_error and any(m in text for m in GUARD_MARKS)
                   else "gate" if is_error and GATE_MARK in text
                   else "ok" if not is_error else "backend-error")
            ok = got == expect
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {tool} {sorted(args)} expect={expect} got={got} :: {text[:110]!r}")
    return bad


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(run(sys.argv[1:])) else 0)
