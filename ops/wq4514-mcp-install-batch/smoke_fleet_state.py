#!/usr/bin/env python3
"""wq#4552 acceptance test for the fleet-state MCP (dbhub -> minerva.db sqlite, read-only via
TOML [[tools]] readonly=true): count open facts, read a known subject, and require every write
attempt to be refused. Exit 0 only if the positive checks pass and every hostile statement
errored."""
import asyncio, os, re, sys, warnings
warnings.filterwarnings("ignore")
LAUNCHER = "/home/lumen/.local/share/mcp/dbhub/1.5.0/launch-fleet-state.sh"
HOSTILE = [
    ("INSERT", "INSERT INTO state_facts (subject, predicate, value, valid_from) "
                "VALUES ('wq4552_probe', 'x', 'x', '2026-01-01')"),
    ("UPDATE", "UPDATE state_facts SET value = value WHERE 1=0"),
    ("DELETE", "DELETE FROM state_facts WHERE 1=0"),
    ("CREATE TABLE", "CREATE TABLE wq4552_probe1 (x int)"),
    ("PRAGMA write toggle", "PRAGMA query_only = 0"),
]
BAD = re.compile(r"error|denied|read-only|readonly|not allowed|not permitted|permission|violation", re.I)


def text(res):
    return " ".join(getattr(c, "text", "") or "" for c in res.content)


async def main():
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    fails = []

    def check(label, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
        if not ok:
            fails.append(label)

    async with Client(StdioTransport(command=LAUNCHER, args=[], env=dict(os.environ)), timeout=120) as c:
        tools = sorted(t.name for t in await c.list_tools())
        check("tools/list through guard", tools == ["execute_sql", "search_objects"], str(tools))
        r = await c.call_tool("execute_sql", {"sql": "SELECT count(*) AS n FROM state_facts WHERE valid_to IS NULL"}, raise_on_error=False)
        m = re.search(r"\b(\d{3,})\b", text(r))
        check("SELECT count(*) open facts", (not r.is_error) and m is not None and int(m.group(1)) >= 1000, text(r)[:120].replace("\n", " "))
        r = await c.call_tool("execute_sql", {"sql": "SELECT subject,predicate,value FROM state_facts WHERE subject='lumen' AND valid_to IS NULL LIMIT 3"}, raise_on_error=False)
        check("SELECT subject=lumen facts", (not r.is_error) and "tailscale" in text(r), text(r)[:120].replace("\n", " "))
        for label, sql in HOSTILE:
            try:
                r = await c.call_tool("execute_sql", {"sql": sql}, raise_on_error=False)
                refused = r.is_error or bool(BAD.search(text(r)))
                detail = text(r)[:110].replace("\n", " ")
            except Exception as exc:
                refused, detail = True, f"{type(exc).__name__}: {str(exc)[:90]}"
            check(f"REFUSED {label}", refused, detail)
    print("RESULT:", "FAIL " + ", ".join(fails) if fails else "ALL PASS")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
