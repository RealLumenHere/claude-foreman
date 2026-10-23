#!/usr/bin/env python3
"""wq#4516 acceptance test for the docker MCP: list containers, read litellm logs (masked), and require
every mutating tool to be refused. Compares `docker ps` before and after. Exit 0 only if all pass."""
import asyncio, json, os, re, subprocess, sys, warnings
warnings.filterwarnings("ignore")
LAUNCHER = "/home/lumen/.local/share/mcp/docker-mcp/0.3.0/launch.sh"
CRED = re.compile(r"sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]{16,}|(?:api[_-]?key|token|password|secret)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}", re.I)
MUTATING = [("run_container", {"image": "alpine", "command": "true"}), ("create_container", {"image": "alpine"}),
            ("start_container", {"container_id": "litellm"}), ("stop_container", {"container_id": "litellm"}),
            ("recreate_container", {"container_id": "litellm"}), ("remove_container", {"container_id": "litellm"}),
            ("pull_image", {"repository": "alpine"}), ("push_image", {"repository": "alpine"}),
            ("build_image", {"path": "/tmp"}), ("remove_image", {"image": "alpine"}),
            ("create_volume", {"name": "wq4516probe"}), ("remove_volume", {"volume_name": "x"}),
            ("create_network", {"name": "wq4516probe"}), ("remove_network", {"network_id": "x"})]


def text(res):
    return " ".join(getattr(c, "text", "") or "" for c in res.content)


def ps():
    return subprocess.run(["docker", "ps", "--format", "{{.Names}} {{.ID}} {{.Status}}"], capture_output=True, text=True).stdout.split("\n")


async def main():
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    fails = []

    def check(label, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
        if not ok:
            fails.append(label)
    before = {l.split()[0]: l.split()[1] for l in ps() if l.strip()}
    async with Client(StdioTransport(command=LAUNCHER, args=[], env=dict(os.environ)), timeout=120) as c:
        tools = {t.name: t for t in await c.list_tools()}
        check("tools/list through guard", sorted(tools) == ["fetch_container_logs", "list_containers", "list_images", "list_networks", "list_volumes"], str(sorted(tools)))
        r = await c.call_tool("list_containers", {}, raise_on_error=False)
        names = sorted(n for n in before if n in text(r))
        check("list_containers sees the running fleet containers", (not r.is_error) and len(names) == len(before), f"{len(names)}/{len(before)}: {', '.join(names)}")
        props = tools["fetch_container_logs"].inputSchema.get("properties", {})
        cid = next((k for k in ("container_id", "container", "name") if k in props), None)
        args = {cid: "litellm", **({"tail": 300} if "tail" in props else {})}
        r = await c.call_tool("fetch_container_logs", args, raise_on_error=False)
        masked = text(r)
        raw = subprocess.run(["docker", "logs", "--tail", "300", "litellm"], capture_output=True, text=True)
        raw_hits = len(CRED.findall(raw.stdout + raw.stderr))
        check("fetch_container_logs litellm", (not r.is_error) and len(masked) > 100, f"{len(masked)} chars returned (args {sorted(args)})")
        check("no credential-shaped strings reach the agent", len(CRED.findall(masked)) == 0, f"raw sample had {raw_hits} credential-shaped matches, masked output has {len(CRED.findall(masked))}" + ("" if raw_hits else "  [raw sample was clean: masking not exercised by live data]"))
        r = await c.call_tool("list_images", {}, raise_on_error=False)
        check("list_images", not r.is_error, text(r)[:80].replace("\n", " "))
        for tool, a in MUTATING:
            try:
                r = await c.call_tool(tool, a, raise_on_error=False)
                refused, detail = r.is_error and "not permitted" in text(r), text(r)[:90]
            except Exception as exc:
                refused, detail = True, f"{type(exc).__name__}: {str(exc)[:80]}"
            check(f"REFUSED {tool}", refused, detail)
    after = {l.split()[0]: l.split()[1] for l in ps() if l.strip()}
    check("docker ps unchanged (same containers, same IDs)", before == after, f"{len(before)} -> {len(after)}")
    print("RESULT:", "FAIL " + ", ".join(fails) if fails else "ALL PASS")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
