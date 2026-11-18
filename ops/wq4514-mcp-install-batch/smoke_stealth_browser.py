#!/usr/bin/env python3
"""wq#4888 acceptance for the guarded stealth-browser launcher.

  smoke_stealth_browser.py [LAUNCHER [ARGS...]]   default: the installed launch.sh

The guard must hide and refuse execute_script and file_upload, refuse a non-http(s) navigate and a
sandbox-off spawn with its OWN message (the entrypoint behind it refuses some of these too, so a
backend refusal does not count), and still drive a real page: spawn, navigate to a public site,
read it back inside the untrusted markers, close. Pointed at the raw entrypoint the denial probes
must FAIL, which is how this probe is shown able to fail. Exit 0 only if every line is ok.
"""
import asyncio
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LAUNCHER = ["/home/lumen/.local/share/mcp/stealth-browser/launch.sh"]
GUARD_MARKS = ("not permitted by the lumen MCP guard", "Unknown tool:",
               "only absolute http(s) URLs with a host are allowed", "is not permitted; allowed:")
HIDDEN = ("execute_script", "file_upload")
DENIALS = [  # every input is harmless if it reaches an unguarded backend
    ("execute_script", {"instance_id": "wq4888-none", "script": "1"}),
    ("file_upload", {"instance_id": "wq4888-none", "selector": "input", "paths": ["/tmp/wq4888-probe-none"]}),
    ("navigate", {"instance_id": "wq4888-none", "url": "file:///tmp/wq4888-probe-none"}),
    ("spawn_browser", {"sandbox": False, "headless": True}),
]
PUBLIC = "https://example.com/"


def text_of(r):
    return " ".join(getattr(x, "text", "") or "" for x in r.content)


async def call(c, tool, args):
    try:  # the guard answers a hidden tool with a JSON-RPC error, not an isError result
        r = await c.call_tool(tool, args, raise_on_error=False)
        return r.is_error, text_of(r)
    except Exception as exc:
        return True, str(exc)


async def run(cmd):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    bad = 0

    def check(ok, line):
        nonlocal bad
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {line}")

    async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=dict(os.environ)), timeout=180) as c:
        names = {t.name for t in await c.list_tools()}
        print(f"tools/list: {len(names)} tools")
        check(len(names) == 19, f"19 tools listed (got {len(names)})")
        for tool in HIDDEN:
            check(tool not in names, f"{tool} not listed")
        for tool, args in DENIALS:
            is_error, text = await call(c, tool, args)
            check(is_error and any(m in text for m in GUARD_MARKS), f"{tool} {sorted(args)} guard-denied :: {text[:90]!r}")
        is_error, text = await call(c, "spawn_browser", {"headless": True})
        iid = next(iter(UUID.findall(text)), "")
        check(not is_error and iid, f"spawn_browser -> {iid[:8] or text[:80]!r}")
        if iid:
            is_error, text = await call(c, "navigate", {"instance_id": iid, "url": PUBLIC})
            check(not is_error, f"navigate {PUBLIC} :: {text[:60]!r}")
            is_error, text = await call(c, "get_page_content", {"instance_id": iid})
            check(not is_error and "Example Domain" in text, "get_page_content reads the public page")
            check("<<<untrusted-" in text, "page content arrives inside the untrusted markers")
        # also closes whatever a probe above spawned when pointed at the raw entrypoint
        for other in set(UUID.findall((await call(c, "list_instances", {}))[1])):
            await call(c, "close_instance", {"instance_id": other})
    return bad


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(run(sys.argv[1:] or LAUNCHER)) else 0)
