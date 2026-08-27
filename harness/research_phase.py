#!/usr/bin/env python3
"""research_phase.py — mandatory prior-art search phase for hardware-RE projects.

wq#991. The action_gate research gate blocks every DEVICE command on a
hardware_class / research-phase-gated project until this script has run and
written a valid marker. This is how the agent CLEARS that gate — by doing the
search, not by escalation.

It does NOT reinvent crawling: it orchestrates fleet-search (wq#999), which is
the fleet's SearXNG + Reddit(old.reddit comment tree) + authenticated-GitHub
surface. This script picks Frank-style short queries, runs them across
web/reddit/github, pulls the top thread's comments, writes a structured
PRIOR_ART.md, and — only if at least one source returned at least one result —
writes projects/<name>/research/.research-complete.json.

Fail-closed: a total search outage writes NO marker and exits non-zero, so
"no research" can never read to the gate as "research done".

  python3 research_phase.py --project miko3
  python3 research_phase.py --project miko3 --queries "miko 3 robot github" "miko unlock"
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

PROJECTS_ROOT = os.environ.get("FOREMAN_PROJECTS_ROOT", "/home/lumen/projects")
FLEET_SEARCH_BIN = os.environ.get("FLEET_SEARCH_BIN", "/home/lumen/bin/fleet-search")
RULES_BASENAME = ".fworker-rules.yaml"
RESEARCH_MARKER = ".research-complete.json"
RESEARCH_SCHEMA = "research-complete/v1"
RE_ANGLES = ["github", "firmware root unlock", "teardown forum xda"]


def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_rules(project):
    path = os.path.join(PROJECTS_ROOT, project, RULES_BASENAME)
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, ImportError, ValueError):
        return {}


def deslug(name):
    """miko3 -> 'miko 3', rk3568-tablet -> 'rk3568 tablet'."""
    s = re.sub(r'[-_]+', ' ', name)
    s = re.sub(r'([A-Za-z])(\d)', r'\1 \2', s)
    return re.sub(r'\s+', ' ', s).strip()


def derive_queries(project, rules):
    """Explicit rules keys win; else derive Frank-style short queries from the
    de-slugged project name + hardware-RE angles."""
    q = rules.get("research_queries")
    if q:
        return [str(x) for x in q][:6]
    subject = str(rules.get("research_subject") or deslug(project))
    return [f"{subject} {angle}" for angle in RE_ANGLES] + [subject]


def fleet_search(kind, query, project, n=6, issues=False):
    """Shell out to fleet-search; return its JSON envelope (dict). Never raises —
    a dead engine is a normal ok:false result, mirroring fleet-search's own
    fail-soft contract."""
    cmd = [sys.executable, FLEET_SEARCH_BIN, "--project", project, kind, query, "--n", str(n)]
    if issues:
        cmd.append("--issues")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return json.loads(p.stdout) if p.stdout.strip() else {
            "source": kind, "ok": False, "error": (p.stderr or "empty output")[:300]}
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        return {"source": kind, "ok": False, "error": f"{type(e).__name__}: {e}"}


def run_phase(project, queries, n=6):
    """Execute the search plan. Returns (runs, results_by_source, top_read)."""
    runs = []            # list of (kind, query, envelope)
    per_source = {}
    # web across every query (discovery); reddit + github repos on the primary subject
    for query in queries:
        runs.append(("web", query, fleet_search("web", query, project, n)))
    primary = queries[-1]
    runs.append(("reddit", primary, fleet_search("reddit", primary, project, n)))
    runs.append(("github", primary, fleet_search("github", primary, project, n)))
    for kind, _q, env in runs:
        if env.get("ok"):
            per_source.setdefault(kind, 0)
            per_source[kind] += len(env.get("results", []))
    # pull the top reddit thread's comments (where the real findings live)
    top_read = None
    for kind, _q, env in runs:
        if kind == "reddit" and env.get("ok"):
            for r in env.get("results", []):
                if "reddit.com" in (r.get("url") or ""):
                    rd = fleet_search_read(r["url"], project)
                    if rd.get("ok"):
                        top_read = {"url": r["url"], "title": r.get("title", ""),
                                    "comment_count": rd.get("comment_count", 0),
                                    "content": (rd.get("content") or "")[:1500],
                                    "comments": rd.get("comments", [])[:8]}
                    break
            break
    return runs, per_source, top_read


def fleet_search_read(url, project):
    cmd = [sys.executable, FLEET_SEARCH_BIN, "--project", project, "read", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return json.loads(p.stdout) if p.stdout.strip() else {"ok": False}
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return {"ok": False}


def write_prior_art(rdir, project, queries, runs, per_source, top_read):
    os.makedirs(rdir, exist_ok=True)
    path = os.path.join(rdir, "PRIOR_ART.md")
    lines = [f"\n## RESEARCH PHASE — {_iso()} (wq#991)\n",
             f"Project: **{project}**  \nQueries: " + "; ".join(f"`{q}`" for q in queries) + "  \n",
             "Source result counts: " + ", ".join(f"{k}={v}" for k, v in sorted(per_source.items()))
             + ("  \n" if per_source else "(none)  \n"), ""]
    seen = set()
    for kind, query, env in runs:
        if not env.get("ok"):
            lines.append(f"- _[{kind}] `{query}` -> {env.get('error','no results')}_")
            continue
        for r in env.get("results", [])[:6]:
            u = r.get("url", "")
            if not u or u in seen:
                continue
            seen.add(u)
            t = (r.get("title", "") or "")[:110]
            extra = ""
            if r.get("stars") is not None:
                extra = f" (★{r.get('stars')})"
            elif r.get("num_comments"):
                extra = f" ({r.get('num_comments')} comments)"
            lines.append(f"- [{kind}] {t}{extra}  \n  {u}")
    if top_read:
        lines.append(f"\n### Top thread — {top_read['title'][:120]}\n{top_read['url']}  ")
        lines.append(f"_{top_read['comment_count']} comments extracted_\n")
        for c in top_read.get("comments", []):
            lines.append(f"> **{c.get('author','?')}**: {c.get('text','')[:300]}")
    lines.append("")
    with open(path, "a") as f:
        f.write("\n".join(lines))
    return path


def write_marker(rdir, project, rules, queries, per_source, findings_path, worker):
    findings_bytes = os.path.getsize(findings_path)
    marker = {
        "schema": RESEARCH_SCHEMA,
        "project": project,
        "hardware_class": rules.get("hardware_class"),
        "completed_at": _iso(),
        "engine": "fleet-search+searxng",
        "queries": queries,
        "sources_ok": sorted(per_source.keys()),
        "result_counts": per_source,
        "total_results": sum(per_source.values()),
        "findings_file": "PRIOR_ART.md",
        "findings_sha256": hashlib.sha256(open(findings_path, "rb").read()).hexdigest(),
        "findings_bytes": findings_bytes,
        "worker": worker,
    }
    path = os.path.join(rdir, RESEARCH_MARKER)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(marker, f, indent=2)
    os.rename(tmp, path)
    return path, marker


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mandatory prior-art research phase (wq#991).")
    ap.add_argument("--project", required=True)
    ap.add_argument("--queries", nargs="*", default=None,
                    help="explicit queries (else derived from the rules file / project name)")
    ap.add_argument("--n", type=int, default=6, help="results per source (default 6)")
    ap.add_argument("--print-queries", action="store_true", help="print derived queries and exit")
    args = ap.parse_args(argv)

    project = args.project
    rules = load_rules(project)
    queries = args.queries or derive_queries(project, rules)
    if args.print_queries:
        print("\n".join(queries))
        return 0

    rdir = os.path.join(PROJECTS_ROOT, project, "research")
    worker = os.environ.get("FOREMAN_WORKER", os.environ.get("WQ_AGENT", "unknown"))
    print(f"[research_phase] project={project} queries={queries}", file=sys.stderr)

    runs, per_source, top_read = run_phase(project, queries, args.n)
    total = sum(per_source.values())
    findings_path = write_prior_art(rdir, project, queries, runs, per_source, top_read)

    if total == 0:
        errs = "; ".join(f"{k}:{e.get('error','?')}" for k, _q, e in runs if not e.get("ok"))
        print(f"[research_phase] ALL SOURCES RETURNED ZERO RESULTS — no marker written "
              f"(fail-closed). errors: {errs[:400]}", file=sys.stderr)
        print(json.dumps({"ok": False, "project": project, "total_results": 0,
                          "marker_written": False}))
        return 2

    marker_path, marker = write_marker(rdir, project, rules, queries, per_source,
                                       findings_path, worker)
    print(f"[research_phase] wrote {findings_path} and {marker_path} "
          f"({total} results across {sorted(per_source)})", file=sys.stderr)
    print(json.dumps({"ok": True, "project": project, "total_results": total,
                      "sources_ok": sorted(per_source.keys()),
                      "findings_file": findings_path, "marker": marker_path,
                      "marker_data": marker}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
