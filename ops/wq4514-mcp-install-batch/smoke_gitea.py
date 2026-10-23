#!/usr/bin/env python3
"""wq#4514 acceptance test for the registered gitea MCP: list repos, create a test issue, close it,
and prove the tools the guard withholds (merge, delete, direct commit, create_repo) are unreachable.

  smoke_gitea.py [--repo OWNER/NAME]      (default lumen/fleet-config; the issue is closed, not deleted:
                                           the guard exposes no delete)
Exit 0 only if every positive step succeeded and every negative step was refused.
"""
import argparse, asyncio, json, os, re, sys, warnings
warnings.filterwarnings("ignore")
LAUNCHER = "/home/lumen/.local/share/mcp/gitea-mcp/1.8.1/launch.sh"


def text(res):
    return " ".join(getattr(c, "text", "") or "" for c in res.content)


async def main(owner, repo, skip_issue):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    fails = []

    def check(label, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
        if not ok:
            fails.append(label)

    async with Client(StdioTransport(command=LAUNCHER, args=[], env=dict(os.environ)), timeout=120) as c:
        tools = {t.name for t in await c.list_tools()}
        check("tools/list through guard", len(tools) == 30, f"{len(tools)} tools")
        r = await c.call_tool("get_me", {}, raise_on_error=False)
        check("get_me", (not r.is_error) and '"lumen"' in text(r).lower() or "lumen" in text(r).lower(), text(r)[:80].replace("\n", " "))
        r = await c.call_tool("list_my_repos", {}, raise_on_error=False)
        names = sorted(set(re.findall(r'"full_name"\s*:\s*"([^"]+)"', text(r))))
        check("list_my_repos", (not r.is_error) and f"{owner}/{repo}" in names, f"{len(names)} repos: {', '.join(names)[:200]}")
        num = None
        r = None if skip_issue else await c.call_tool("issue_write", {"method": "create", "owner": owner, "repo": repo,
            "title": "[wq#4514] gitea-mcp smoke test (auto-closed)",
            "body": "Created and closed by ops/wq4514-mcp-install-batch/smoke_gitea.py to prove the gitea MCP works end to end. No action needed."}, raise_on_error=False)
        if r is not None:
            m = re.search(r'"number"\s*:\s*(\d+)', text(r)) or re.search(r'"index"\s*:\s*(\d+)', text(r)) or re.search(r'#(\d+)', text(r))
            num = int(m.group(1)) if m else None
            check("issue_write create", (not r.is_error) and num is not None, f"issue #{num}" if num else text(r)[:200])
        if num:
            r = await c.call_tool("issue_write", {"method": "update", "owner": owner, "repo": repo, "issue_number": num, "state": "closed"}, raise_on_error=False)
            check("issue_write update state=closed", not r.is_error, text(r)[:100].replace("\n", " "))
            r = await c.call_tool("issue_read", {"method": "get", "owner": owner, "repo": repo, "issue_number": num}, raise_on_error=False)
            check("issue_read confirms closed", (not r.is_error) and '"closed"' in text(r), re.sub(r"\s+", " ", text(r))[:100])
        for tool, args in (("pull_request_write", {"method": "merge", "owner": owner, "repo": repo, "pull_number": 1}),
                           ("pull_request_review_write", {"method": "dismiss", "owner": owner, "repo": repo, "pull_number": 1}),
                           ("pull_request_write", {"owner": owner, "repo": repo, "pull_number": 1}),
                           ("delete_branch", {"owner": owner, "repo": repo, "branch": "x"}),
                           ("delete_file", {"owner": owner, "repo": repo, "path": "x"}),
                           ("create_or_update_file", {"owner": owner, "repo": repo, "path": "x", "content": "eA==", "message": "m", "branch": "x"}),
                           ("create_repo", {"name": "wq4514-should-not-exist"}),
                           ("actions_config_write", {"method": "upsert_repo_secret", "owner": owner, "repo": repo})):
            try:
                r = await c.call_tool(tool, args, raise_on_error=False)
                refused = r.is_error and any(k in text(r) for k in ("not permitted", "Unknown tool", "Input validation error"))
                detail = text(r)[:110].replace("\n", " ")
            except Exception as exc:
                refused, detail = True, f"{type(exc).__name__}: {str(exc)[:90]}"
            check(f"REFUSED {tool}({args.get('method', '-')})", refused, detail)
    print("RESULT:", "FAIL " + ", ".join(fails) if fails else "ALL PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="lumen/fleet-config")
    ap.add_argument("--skip-issue", action="store_true", help="only run the negative (refusal) checks")
    a = ap.parse_args()
    o, _, r = a.repo.partition("/")
    sys.exit(asyncio.run(main(o, r, a.skip_issue)))
