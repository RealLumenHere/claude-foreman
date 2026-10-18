"""Live acceptance probe for the installed stealth-browser MCP server (wq#4206).

Speaks MCP over stdio to the same command the claude registration runs, so it tests
the installed server and guard, not a copy. Run with the install's venv python:

  <DEST>/venv/bin/python -I probe.py <DEST>

Exit 0 only when every policy case refuses and the public-web cases work. The
anti-bot sites at the end are reported, not asserted: they show what the browser gets
past today, and their answers change with the sites.
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

DEST = Path(sys.argv[1]).resolve()
SENTINEL = "wq4206-loopback-sentinel"
ANTI_BOT_SITES = ("https://nowsecure.nl/", "https://www.crunchbase.com/organization/anthropic")
BLOCK_MARKERS = ("Just a moment", "security service", "Verify you are human", "Attention Required")
failures = []


def report(case, ok, detail=""):
    print(json.dumps({"case": case, "ok": ok, "detail": str(detail)[:300]}), flush=True)
    if not ok:
        failures.append(case)


class Sentinel(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"<html><title>{SENTINEL}</title><body>{SENTINEL}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def text(result):
    return "".join(getattr(block, "text", "") for block in result.content)


async def expect_refused(client, case, tool, args, needle="lumen policy"):
    try:
        result = await client.call_tool(tool, args)
    except ToolError as exc:
        report(case, needle in str(exc), exc)
        return
    report(case, False, f"not refused: {text(result)}")


async def main():
    server = HTTPServer(("127.0.0.1", 0), Sentinel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    loopback = f"http://127.0.0.1:{server.server_port}/"

    transport = StdioTransport(str(DEST / "venv" / "bin" / "python"),
                               ["-I", str(DEST / "lumen_stealth_mcp.py"), "--minimal"])
    async with Client(transport, timeout=120) as client:
        names = sorted(tool.name for tool in await client.list_tools())
        report("tools/list minimal surface", len(names) == 21 and "spawn_browser" in names
               and not any("hook" in name for name in names), f"{len(names)}: {names}")

        await expect_refused(client, "spawn refuses --remote-debugging-address", "spawn_browser",
                             {"browser_args": ["--remote-debugging-address=0.0.0.0"]})
        await expect_refused(client, "spawn refuses profile outside install", "spawn_browser",
                             {"user_data_dir": "/home/lumen/kb"})

        spawned = json.loads(text(await client.call_tool("spawn_browser", {})))
        iid = spawned["instance_id"]
        report("spawn_browser forced headless", spawned.get("headless") is True, spawned)

        nav = json.loads(text(await client.call_tool("navigate", {"instance_id": iid, "url": "https://example.com/"})))
        report("navigate public https", nav.get("title") == "Example Domain", nav)
        content = text(await client.call_tool("get_page_content", {"instance_id": iid}))
        report("get_page_content public", "Example Domain" in content, content[:120])

        await expect_refused(client, "navigate refuses file:// (granted non-cwd dir)", "navigate",
                             {"instance_id": iid, "url": "file:///home/lumen/kb/ISSUE-LOG.md"})
        await expect_refused(client, "navigate refuses loopback", "navigate", {"instance_id": iid, "url": loopback})
        await expect_refused(client, "navigate refuses tailnet IP", "navigate",
                             {"instance_id": iid, "url": "http://100.117.66.102/"})
        await expect_refused(client, "navigate refuses decimal loopback", "navigate",
                             {"instance_id": iid, "url": "http://2130706433/"})
        await expect_refused(client, "screenshot refuses path outside install", "take_screenshot",
                             {"instance_id": iid, "file_path": "/home/lumen/kb/wq4206-probe.png"})
        shot = DEST / "screenshots" / "wq4206-probe.png"
        await client.call_tool("take_screenshot", {"instance_id": iid, "file_path": str(shot)})
        report("screenshot inside install dir", shot.exists() and shot.stat().st_size > 0, shot)

        # Page script moves the tab to loopback behind the guard's back. Whichever call
        # first sees the tab there (the script call itself if the hop is fast, else the
        # next one) must blank the tab and withhold its content.
        case = "script-driven loopback landing withheld"
        try:
            await client.call_tool("execute_script", {"instance_id": iid, "script": f"location.href = {loopback!r}"})
            await asyncio.sleep(3)
            leaked = text(await client.call_tool("get_page_content", {"instance_id": iid}))
            report(case, False, f"returned: {leaked[:120]}")
        except ToolError as exc:
            report(case, "landed on" in str(exc) and SENTINEL not in str(exc), exc)
        state = text(await client.call_tool("get_page_content", {"instance_id": iid}))
        report("tab blanked after private landing", SENTINEL not in state, state[:120])

        for site in ANTI_BOT_SITES:
            try:
                await client.call_tool("navigate", {"instance_id": iid, "url": site, "timeout": 45000})
                await asyncio.sleep(8)
                page = text(await client.call_tool("get_page_content", {"instance_id": iid}))
                blocked = [m for m in BLOCK_MARKERS if m in page]
                print(json.dumps({"anti_bot": site, "chars": len(page), "block_markers": blocked}), flush=True)
            except ToolError as exc:
                print(json.dumps({"anti_bot": site, "error": str(exc)[:200]}), flush=True)

        await client.call_tool("close_instance", {"instance_id": iid})
    server.shutdown()
    print(json.dumps({"probe": "FAIL" if failures else "PASS", "failures": failures}))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
