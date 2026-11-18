#!/usr/bin/env python3
"""wq#4888 acceptance for the guarded council launcher.

  smoke_council.py [--no-spend] [LAUNCHER [ARGS...]]   default: the installed launch.sh

The guard must refuse an ask_deepseek model override outside flash|pro with its OWN message: a free
value goes straight to LiteLLM, which routes anthropic/* (rule 14). The probe's value names no real
model, so pointed at the raw server it costs nothing: LiteLLM answers that the model does not exist,
and the probe FAILs, which is how it is shown able to fail. Without --no-spend one real ask_deepseek
(default model, a few tokens) proves a long call still passes through. Exit 0 only if every line is ok.
"""
import asyncio
import os
import sys
import warnings

warnings.filterwarnings("ignore")

LAUNCHER = ["/home/lumen/.local/share/mcp/council/launch.sh"]
GUARD_MARKS = ("not permitted by the lumen MCP guard", "Unknown tool:", "is not permitted; allowed:")
TOOLS = {"ask_deepseek", "ask_gemini", "ask_codex", "review_code", "council_status"}


async def call(c, tool, args):
    try:
        r = await c.call_tool(tool, args, raise_on_error=False)
        return r.is_error, " ".join(getattr(x, "text", "") or "" for x in r.content)
    except Exception as exc:
        return True, str(exc)


async def run(cmd, spend):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    bad = 0

    def check(ok, line):
        nonlocal bad
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {line}")

    async with Client(StdioTransport(command=cmd[0], args=cmd[1:], env=dict(os.environ)), timeout=300) as c:
        names = {t.name for t in await c.list_tools()}
        print(f"tools/list: {len(names)} tools")
        check(names == TOOLS, f"exactly the 5 council tools listed (got {sorted(names)})")
        for model in ("anthropic/wq4888-probe-nonexistent", "lumen/wq4888-probe-nonexistent"):
            is_error, text = await call(c, "ask_deepseek", {"prompt": "x", "model": model})
            check(is_error and any(m in text for m in GUARD_MARKS), f"ask_deepseek model={model} guard-denied :: {text[:90]!r}")
        is_error, text = await call(c, "council_status", {})
        check(not is_error and "<<<untrusted-" in text, "council_status answers inside the untrusted markers")
        if spend:
            is_error, text = await call(c, "ask_deepseek", {"prompt": "Reply with the single word PONG."})
            check(not is_error and "PONG" in text.upper(), f"ask_deepseek (default model) answers :: {text[-60:]!r}")
    return bad


if __name__ == "__main__":
    argv = sys.argv[1:]
    spend = "--no-spend" not in argv
    argv = [a for a in argv if a != "--no-spend"]
    sys.exit(1 if asyncio.run(run(argv or LAUNCHER, spend)) else 0)
