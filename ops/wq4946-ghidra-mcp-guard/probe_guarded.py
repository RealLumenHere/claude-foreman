#!/usr/bin/env python3
"""wq#4946 acceptance probe: drive a ghidra-lumen launcher over stdio and check the guard's decisions.

  probe_guarded.py LAUNCHER [LAUNCHER ARGS...]

Run with GHIDRA_MCP_AUTOSTART=0. A guard denial carries the guard's own message; an allowed call
reaches the bridge and fails there (no Ghidra: connection error; live Ghidra: "File not found",
"No program loaded"). That difference is what is asserted. The two allowed probes cannot change
anything: a load of a nonexistent file and a read-only listing.
Exit 0 when every expectation holds; prints one line per probe.
"""
import asyncio
import os
import sys
import warnings

warnings.filterwarnings("ignore")

GUARD_MARKS = ("not permitted by the lumen MCP guard", "is outside", "must be an absolute path")

PROBES = [  # (tool, args, expect_guard_denial)
    ("run_script_inline", {"code": "x"}, True),
    ("run_ghidra_script", {"script_name": "x"}, True),
    ("connect_instance", {"project": "x"}, True),
    ("export_program", {"program_name": "x", "output_dir": "/tmp"}, True),
    ("checkin_program", {"path": "/x"}, True),
    ("search_tools", {"query": "script"}, True),
    # Out-of-root inputs that are harmless if the probe is ever pointed at an UNguarded bridge
    # (the falsification run): nothing secret-bearing, nothing that exists.
    ("load_program", {"file": "/tmp/wq4946-probe-nonexistent.bin"}, True),
    ("load_program", {"file": "/home/lumen/re/../wq4946-probe-nonexistent"}, True),
    ("import_file", {"file_path": "/etc/hostname"}, True),
    ("open_project", {"path": "relative/proj"}, True),
    ("load_program", {"file": "/home/lumen/re/nonexistent.bin"}, False),
    ("list_functions", {}, False),
]


async def run(cmd):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    bad = 0
    async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=dict(os.environ)), timeout=120) as c:
        names = {t.name for t in await c.list_tools()}
        print(f"tools/list: {len(names)} tools")
        for hidden in ("run_script_inline", "connect_instance", "search_tools", "checkin_program"):
            if hidden in names:
                print(f"FAIL listed: {hidden}")
                bad += 1
        for tool, args, expect in PROBES:
            r = await c.call_tool(tool, args, raise_on_error=False)
            text = " ".join(getattr(x, "text", "") or "" for x in r.content)
            denied = r.is_error and any(m in text for m in GUARD_MARKS)
            ok = denied == expect
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {tool} {sorted(args)} guard_denied={denied} :: {text[:90]!r}")
    return bad


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(run(sys.argv[1:])) else 0)
