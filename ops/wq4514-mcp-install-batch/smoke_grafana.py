#!/usr/bin/env python3
"""wq#4518 acceptance test for the grafana MCP (Bes Grafana, anonymous Viewer): datasources, dashboards,
current alert-rule state, a metrics query, and refusal of every write/escape tool. Exit 0 only if all pass."""
import asyncio, json, os, re, sys, urllib.request, warnings
warnings.filterwarnings("ignore")
LAUNCHER = "/home/lumen/.local/share/mcp/mcp-grafana/2.0.2/launch.sh"
GRAFANA = "http://100.110.156.91:3000"
DENIED = [("grafana_api_request", {"method": "POST", "path": "/api/folders", "body": "{\"title\":\"wq4518probe\"}"}),
          ("create_datasource", {"name": "wq4518probe", "type": "prometheus"}),
          ("update_dashboard", {"dashboard": {"title": "wq4518probe"}}),
          ("create_folder", {"title": "wq4518probe"}),
          ("install_plugin", {"pluginId": "x"}),
          ("create_annotation", {"text": "wq4518probe"}),
          ("delete_annotation", {"id": 1}),
          ("alerting_rules_write", {}),
          ("alerting_silences_write", {})]


def text(res):
    return " ".join(getattr(c, "text", "") or "" for c in res.content)


def api(path):
    with urllib.request.urlopen(GRAFANA + path, timeout=15) as r:
        return json.load(r)


async def main():
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    fails = []

    def check(label, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
        if not ok:
            fails.append(label)
    dash_before = sorted(d["uid"] for d in api("/api/search?type=dash-db"))
    folders_before = len(api("/api/folders"))
    async with Client(StdioTransport(command=LAUNCHER, args=[], env=dict(os.environ)), timeout=120) as c:
        tools = {t.name: t for t in await c.list_tools()}
        check("tools/list through guard", len(tools) == 19, f"{len(tools)} tools")
        for n in ("alerting_rules_read", "query_prometheus"):
            s = tools[n].inputSchema
            print(f"      schema {n}: props={sorted(s.get('properties', {}))} required={s.get('required')}")
        r = await c.call_tool("list_datasources", {}, raise_on_error=False)
        uids = re.findall(r'"uid"\s*:\s*"([^"]+)"', text(r))
        check("list_datasources", (not r.is_error) and "victoriametrics" in uids, f"uids={uids}")
        r = await c.call_tool("search_dashboards", {"query": "fleet"}, raise_on_error=False)
        found = sorted(set(re.findall(r'"uid"\s*:\s*"([^"]+)"', text(r))))
        check("search_dashboards 'fleet'", (not r.is_error) and "fleet-power" in found, f"uids={found}")
        ops = tools["alerting_rules_read"].inputSchema["properties"]["operation"].get("enum") or ["list"]
        print(f"      alerting_rules_read operations: {ops}")
        r = await c.call_tool("alerting_rules_read", {"operation": "list" if "list" in ops else ops[0]}, raise_on_error=False)
        check("alerting_rules_read (current alert state)", not r.is_error, re.sub(r"\s+", " ", text(r))[:180])
        r = await c.call_tool("query_prometheus", {"datasourceUid": "victoriametrics", "expr": "count(up)", "queryType": "instant", "endTime": "now"}, raise_on_error=False)
        check("query_prometheus count(up) on victoriametrics", (not r.is_error) and "error" not in text(r).lower()[:200], re.sub(r"\s+", " ", text(r))[:180])
        for tool, a in DENIED:
            try:
                r = await c.call_tool(tool, a, raise_on_error=False)
                refused, detail = r.is_error and "not permitted" in text(r), text(r)[:90]
            except Exception as exc:
                refused, detail = True, f"{type(exc).__name__}: {str(exc)[:80]}"
            check(f"REFUSED {tool}", refused, detail)
    check("Bes Grafana unchanged (dashboards + folders)", sorted(d["uid"] for d in api("/api/search?type=dash-db")) == dash_before and len(api("/api/folders")) == folders_before, f"{len(dash_before)} dashboards, {folders_before} folders")
    print("RESULT:", "FAIL " + ", ".join(fails) if fails else "ALL PASS")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
