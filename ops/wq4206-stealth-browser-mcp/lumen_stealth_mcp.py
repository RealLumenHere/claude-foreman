#!/usr/bin/env python3
"""Lumen entrypoint for stealth-browser-mcp (wq#4206).

Runs upstream's src/server.py unchanged, after patching fastmcp's FunctionTool.run so
every tool call goes through lumen_stealth_guard first, and every call on a browser
instance is followed by a check of where the tab really is. A redirect or page script
that lands the tab on a local file or private host blanks the tab and fails the call,
so its content never reaches the agent. Every Chrome it spawns sends all its traffic
through lumen_egress_proxy, which refuses private hosts per connection (wq#4290).

Also pins the upstream knobs agents should not choose: the Chrome binary (the
playwright-installed Chrome for Testing under ./browsers), the upload root, and the
dynamic-hooks section, which exec()s agent-supplied Python inside this process and is
always disabled here.

  lumen_stealth_mcp.py [upstream server.py args]   serve MCP on stdio
  lumen_stealth_mcp.py --print-browser             print the Chrome path and exit
"""
import glob
import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lumen_egress_proxy  # noqa: E402
import lumen_stealth_guard as guard  # noqa: E402

UPSTREAM = HERE / "repo" / "src"
ROOTS = [HERE / "uploads", HERE / "screenshots", HERE / "profiles"]


def browser_path():
    found = sorted(glob.glob(str(HERE / "browsers" / "chromium-*" / "chrome-linux64" / "chrome")))
    return found[-1] if found else ""


def install_guard(egress_proxy):
    from fastmcp.exceptions import ToolError
    from fastmcp.tools.tool import FunctionTool

    has_display = bool(os.environ.get("DISPLAY"))
    original_run = FunctionTool.run

    async def guarded_run(self, arguments):
        try:
            arguments = guard.check_arguments(self.name, arguments, ROOTS, has_display, egress_proxy)
        except guard.PolicyError as exc:
            raise ToolError(f"lumen policy refused {self.name}: {exc}") from None
        result = await original_run(self, arguments)
        instance_id = arguments.get("instance_id")
        if instance_id and self.name != "close_instance":
            await _check_landing(self.fn.__globals__["browser_manager"], instance_id, ToolError)
        return result

    FunctionTool.run = guarded_run


async def _check_landing(manager, instance_id, error):
    tab = await manager.get_tab(instance_id)
    if tab is None:
        return
    url = await tab.evaluate("window.location.href")
    if guard.landed_somewhere_private(url):
        await manager.navigate(instance_id, "about:blank")
        raise error(f"lumen policy: tab landed on {url!r} (local file or private host); "
                    "page blanked and result withheld")


def main():
    if sys.argv[1:] == ["--print-browser"]:
        print(browser_path())
        return
    chrome = browser_path()
    if chrome:
        os.environ["STEALTH_BROWSER_EXECUTABLE"] = chrome
    os.environ["BROWSER_FILE_UPLOAD_ALLOWED_DIRS"] = str(HERE / "uploads")
    for root in ROOTS:
        root.mkdir(exist_ok=True)
    os.chdir(HERE)
    sys.path.insert(0, str(UPSTREAM))
    install_guard(lumen_egress_proxy.start())
    sys.argv = [str(UPSTREAM / "server.py"), *sys.argv[1:], "--disable-dynamic-hooks"]
    runpy.run_path(str(UPSTREAM / "server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
