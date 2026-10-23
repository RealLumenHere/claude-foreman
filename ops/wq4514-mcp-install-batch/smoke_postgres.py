#!/usr/bin/env python3
"""wq#4515 acceptance test for the fleet-memory MCP (dbhub -> mcp_ro): count rows, then try every
write/escalation route and require each to be refused. Independent proof that nothing changed comes
from the superuser side (see RUNBOOK.md): row count unchanged, no wq4515_probe* tables.
Exit 0 only if the positive checks pass and every hostile statement errored."""
import asyncio, os, re, sys, warnings
warnings.filterwarnings("ignore")
LAUNCHER = "/home/lumen/.local/share/mcp/dbhub/1.5.0/launch.sh"
HOSTILE = [
    ("INSERT", "INSERT INTO fleet_facts DEFAULT VALUES"),
    ("UPDATE", "UPDATE fleet_facts SET status = status WHERE false"),
    ("DELETE", "DELETE FROM fleet_facts WHERE false"),
    ("CREATE TABLE", "CREATE TABLE wq4515_probe1 (x int)"),
    ("DROP (nonexistent)", "DROP TABLE IF EXISTS wq4515_probe_nonexistent"),
    ("GRANT", "GRANT ALL ON fleet_facts TO PUBLIC"),
    ("read-only bypass via SET", "SET default_transaction_read_only = off; CREATE TABLE wq4515_probe2 (x int)"),
    ("read-only bypass via BEGIN READ WRITE", "BEGIN READ WRITE; CREATE TABLE wq4515_probe3 (x int); COMMIT"),
    ("pg_authid (password hashes)", "SELECT rolname, rolpassword FROM pg_authid"),
    ("server file read", "SELECT pg_read_file('/etc/passwd')"),
    ("COPY PROGRAM", "COPY (SELECT 1) TO PROGRAM 'id'"),
]
BAD = re.compile(r"error|denied|read-only|read only|not allowed|not permitted|permission|cannot|must be", re.I)


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
        r = await c.call_tool("execute_sql", {"sql": "SELECT count(*) AS n FROM fleet_facts"}, raise_on_error=False)
        m = re.search(r"\b(\d{2,})\b", text(r))
        check("SELECT count(*) FROM fleet_facts", (not r.is_error) and m is not None and int(m.group(1)) >= 300, text(r)[:120].replace("\n", " "))
        r = await c.call_tool("execute_sql", {"sql": "SELECT current_user, current_setting('default_transaction_read_only') AS ro"}, raise_on_error=False)
        check("runs as mcp_ro", "mcp_ro" in text(r), text(r)[:120].replace("\n", " "))
        r = await c.call_tool("search_objects", {"object_type": "table", "detail_level": "names"}, raise_on_error=False)
        check("search_objects lists tables", (not r.is_error) and "fleet_facts" in text(r), text(r)[:100].replace("\n", " "))
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
