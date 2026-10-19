#!/usr/bin/env python3
"""wq#4330 acceptance tests: read/write files on Lumen via Remote DC relay, with latency.

Calls https://mcp.desktopcommander.app/mcp (Streamable HTTP, bearer = Supabase access token).
Prints per-call latency and a JSON summary; writes results.json.
"""
import json
import statistics
import time
import urllib.request
import urllib.error

RUN = "/tmp/fworker-wq4330/rdc"
MCP = "https://mcp.desktopcommander.app/mcp"
TOK = json.load(open(f"{RUN}/tokens.json"))["access_token"]

_session = {"id": None}


def rpc(method, params=None, _id=[0]):
    _id[0] += 1
    body = {"jsonrpc": "2.0", "id": _id[0], "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(MCP, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Authorization", "Bearer " + TOK)
    if _session["id"]:
        req.add_header("mcp-session-id", _session["id"])
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
        sid = r.headers.get("mcp-session-id")
        if sid:
            _session["id"] = sid
        ctype = r.headers.get("Content-Type", "")
    ms = (time.perf_counter() - t0) * 1000
    if "text/event-stream" in ctype:
        data = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
        resp = data
    else:
        resp = json.loads(raw) if raw else {}
    return ms, resp


def tool_text(resp):
    try:
        return "".join(c.get("text", "") for c in resp["result"]["content"])
    except (KeyError, TypeError):
        return json.dumps(resp)[:300]


results = {}

ms, resp = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                              "clientInfo": {"name": "wq4330-probe", "version": "1.0"}})
results["initialize_ms"] = round(ms, 1)
print(f"initialize: {ms:.0f}ms")
rpc("notifications/initialized")

ms, resp = rpc("tools/list")
names = [t["name"] for t in resp["result"]["tools"]]
results["tools_list_ms"] = round(ms, 1)
results["tool_names"] = names
print(f"tools/list: {ms:.0f}ms, {len(names)} tools")

# WRITE: create a file on Lumen in the worktree
probe_path = "/home/lumen/worktrees/wq4330-a1/rdc_write_probe.txt"
content = f"wq4330 remote-dc write probe at {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n"
ms, resp = rpc("tools/call", {"name": "write_file",
                              "arguments": {"path": probe_path, "content": content, "mode": "rewrite"}})
results["write_file_ms"] = round(ms, 1)
print(f"write_file: {ms:.0f}ms -> {tool_text(resp)[:120]}")

# READ BACK: verify round-trip content
ms, resp = rpc("tools/call", {"name": "read_file", "arguments": {"path": probe_path}})
rt = tool_text(resp)
results["read_back_ms"] = round(ms, 1)
results["write_read_roundtrip_ok"] = content.strip() in rt
print(f"read_file(probe): {ms:.0f}ms, roundtrip_ok={results['write_read_roundtrip_ok']}")

# OUTSIDE-WORKTREE READ (probe that can fail: file not under the worktree the
# ticket scoped, and not created by this test): kb/ISSUE-LOG.md
ms, resp = rpc("tools/call", {"name": "read_file",
                              "arguments": {"path": "/home/lumen/kb/ISSUE-LOG.md", "length": 5}})
head = tool_text(resp)
results["read_kb_ms"] = round(ms, 1)
results["read_kb_ok"] = "ISSUE-LOG" in head or "issue" in head.lower() or "|" in head
print(f"read_file(kb/ISSUE-LOG.md): {ms:.0f}ms ok={results['read_kb_ok']}")

# Latency distribution: 10x small read
lat = []
for _ in range(10):
    ms, _r = rpc("tools/call", {"name": "read_file", "arguments": {"path": probe_path}})
    lat.append(ms)
results["read_latency_ms"] = {
    "min": round(min(lat), 1), "median": round(statistics.median(lat), 1),
    "mean": round(statistics.mean(lat), 1), "max": round(max(lat), 1), "n": len(lat),
}
print("read x10 latency ms:", results["read_latency_ms"])

# Terminal path too: start_process a quick command
ms, resp = rpc("tools/call", {"name": "start_process",
                              "arguments": {"command": "hostname && id -un", "timeout_ms": 10000}})
out = tool_text(resp)
results["start_process_ms"] = round(ms, 1)
results["start_process_out_has_host"] = "lumen" in out.lower()
print(f"start_process(hostname): {ms:.0f}ms, lumen_in_output={results['start_process_out_has_host']}")

with open(f"{RUN}/results.json", "w") as f:
    json.dump(results, f, indent=2)
print("saved ->", f"{RUN}/results.json")
