#!/usr/bin/env python3
"""wq#1717 — measure what github_scout's SKIP_PATTERNS throw away, and what one
hub hop recovers.

Reproduces a scout-shaped GitHub search (lumen_scout_graph.py:147), applies the
scout's own scorer (:183-194) to find the candidates it hard-penalizes, then does a
depth-1 expansion of each penalized curated list and counts the distinct repos and
papers it yields. Writes JSON to stdout; no writes outside the repo.

Unauthenticated by design — that is the state the scout itself runs in today
(`"authenticated": false` from fleet-search, and a 403 on topic 11 of 14 in
/home/lumen/agent/logs/scout_graph.log for the 2026-09-10 run).
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = "LumenResearch/wq1717"
TIMEOUT = 25

# verbatim from lumen_scout_graph.py:57-70
RELEVANT_KEYWORDS = [
    "mcp", "homelab", "self-host", "selfhost", "agent", "llm", "ai", "docker",
    "automation", "monitoring", "proxmox", "n8n", "langchain", "langgraph",
    "ollama", "litellm", "openwebui", "open-webui", "workflow", "orchestrat",
    "inference", "local", "privacy", "open-source", "self-hosted", "cli",
    "terminal", "gpu", "rag", "vector", "embedding", "api",
    "server", "deploy", "kubernetes", "k8s", "ansible",
]
SKIP_PATTERNS = [
    "awesome-", "tutorial", "course", "learn", "beginner",
    "cookbook", "prompt-collection", "system-prompt",
    "anime", "game", "music", "art", "image-gen", "stable-diffusion",
]


def get(url, accept="application/vnd.github.v3+json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # network-level
        return 0, str(e).encode()


def scout_score(repo):
    """lumen_scout_graph.py:178-191, verbatim logic."""
    combined = " ".join([
        repo.get("full_name", ""),
        repo.get("description") or "",
        " ".join(repo.get("topics") or []),
    ]).lower()
    score = sum(1 for kw in RELEVANT_KEYWORDS if kw in combined)
    score -= 5 * sum(1 for pat in SKIP_PATTERNS if pat in combined)
    return score


def scout_query(topic, days=7, stars=50, per_page=10):
    """lumen_scout_graph.py:147,151 — same q, same sort, same page size."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    qs = urllib.parse.urlencode({
        "q": f"topic:{topic} pushed:>{since} stars:>{stars}",
        "sort": "stars", "order": "desc", "per_page": per_page,
    })
    status, body = get("https://api.github.com/search/repositories?" + qs)
    if status != 200:
        return status, []
    return status, json.loads(body).get("items", [])


PAPER_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")
REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:[)#?\s/\"']|$)")


def expand_hub(full_name):
    """Depth-1 hop: fetch the list's README, harvest distinct repos + arxiv ids.

    This is the whole of the 'crawl' we are arguing for — one request, no frontier,
    no politeness scheduler, no extraction model.
    """
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{full_name}/{branch}/README.md"
        t0 = time.time()
        status, body = get(url, accept="text/plain")
        if status == 200:
            text = body.decode("utf-8", "replace")
            repos = {r for r in REPO_RE.findall(text) if not r.endswith(".git")}
            repos.discard(full_name)
            return {
                "hub": full_name, "branch": branch, "ok": True,
                "bytes": len(body), "seconds": round(time.time() - t0, 3),
                "repos": sorted(repos), "papers": sorted(set(PAPER_RE.findall(text))),
            }
    return {"hub": full_name, "ok": False, "error": f"no README.md on main/master (last http {status})"}


def main():
    topics = sys.argv[1:] or ["mcp", "ai-agent", "llm-tools"]
    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "authenticated": False,
        "topics": topics,
        "candidates": 0,
        "dropped_by_skip_patterns": [],
        "kept_top5_by_scout_sort": [],
        "expansions": [],
    }
    candidates, errors = {}, []
    for topic in topics:
        status, items = scout_query(topic)
        if status != 200:
            errors.append({"topic": topic, "http": status})
        for repo in items:
            candidates.setdefault(repo["full_name"], repo)
        time.sleep(1)  # scout does the same (:166)
    out["errors"] = errors
    out["candidates"] = len(candidates)

    scored = []
    for repo in candidates.values():
        scored.append({
            "full_name": repo["full_name"],
            "stars": repo.get("stargazers_count", 0),
            "score": scout_score(repo),
            "description": (repo.get("description") or "")[:100],
        })
    # scout's own sort key (:194)
    scored.sort(key=lambda r: (r["score"], r["stars"]), reverse=True)

    out["kept_top5_by_scout_sort"] = scored[:5]
    # the scout drops anything scoring < 1 (:207); a single SKIP_PATTERN hit (-5)
    # is enough to guarantee that for most curated lists.
    out["dropped_by_skip_patterns"] = [r for r in scored if r["score"] < 1]

    for repo in out["dropped_by_skip_patterns"][:5]:
        out["expansions"].append(expand_hub(repo["full_name"]))
        time.sleep(1)

    yielded_repos, yielded_papers = set(), set()
    for e in out["expansions"]:
        if e.get("ok"):
            yielded_repos.update(e["repos"])
            yielded_papers.update(e["papers"])
    out["expansion_totals"] = {
        "hubs_fetched": sum(1 for e in out["expansions"] if e.get("ok")),
        "distinct_repos": len(yielded_repos),
        "distinct_papers": len(yielded_papers),
        "repos_not_in_candidate_pool": len(yielded_repos - set(candidates)),
        "bytes": sum(e.get("bytes", 0) for e in out["expansions"]),
        "seconds": round(sum(e.get("seconds", 0) for e in out["expansions"]), 3),
    }
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
