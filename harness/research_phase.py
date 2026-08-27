#!/usr/bin/env python3
"""research_phase.py — the prior-art search phase for hardware-RE projects.

wq#991 deliverable #3 ("search like Frank: short targeted queries, follow the
links, read the comments — not one broad search and give up"). The device gate
itself lives in action_gate.py (wq#1006): a hardware_class project blocks every
device command until projects/<name>/research/PRIOR_ART.md is non-empty. This
script is the one command an agent runs to PRODUCE that file well, instead of
hand-running a single fleet-search and hoping.

It does not reinvent crawling: it orchestrates fleet-search (wq#999), the
fleet's SearXNG + Reddit(old.reddit comment tree) + authenticated-GitHub
surface. It derives a handful of angled queries, runs them across web/reddit/
github, pulls the top reddit thread's comments (where the real findings live),
and writes one structured section to PRIOR_ART.md.

Fail-closed: if every source returns zero results, it writes NOTHING and exits
non-zero, so a dead search engine can never leave a non-empty PRIOR_ART.md that
reads to the gate as "research done".

  python3 research_phase.py --project miko3
  python3 research_phase.py --project miko3 --queries "miko 3 robot github" "miko unlock"
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

PROJECTS_ROOT = os.environ.get("FOREMAN_PROJECTS_ROOT", "/home/lumen/projects")
FLEET_SEARCH_BIN = os.environ.get("FLEET_SEARCH_BIN", "/home/lumen/bin/fleet-search")
RULES_BASENAME = ".fworker-rules.yaml"
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
    """miko3 -> 'miko 3', rk3568-tablet -> 'rk 3568 tablet'."""
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


def fleet_search(kind, query, n=6, issues=False):
    """Shell out to fleet-search; return its JSON envelope (dict). No --project:
    the search returns pure JSON and this script is the sole PRIOR_ART.md writer
    (passing --project makes fleet-search append its own duplicate sections).
    Never raises — a dead engine is a normal ok:false result, mirroring
    fleet-search's own fail-soft contract."""
    cmd = [sys.executable, FLEET_SEARCH_BIN, kind, query, "--n", str(n)]
    if issues:
        cmd.append("--issues")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return json.loads(p.stdout) if p.stdout.strip() else {
            "source": kind, "ok": False, "error": (p.stderr or "empty output")[:300]}
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        return {"source": kind, "ok": False, "error": f"{type(e).__name__}: {e}"}


def fleet_search_read(url):
    cmd = [sys.executable, FLEET_SEARCH_BIN, "read", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return json.loads(p.stdout) if p.stdout.strip() else {"ok": False}
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return {"ok": False}


def run_phase(project, queries, n=6):
    """Execute the search plan. Returns (runs, results_by_source, top_read)."""
    runs = []            # list of (kind, query, envelope)
    per_source = {}
    # web across every query (discovery); reddit + github repos on the primary subject
    for query in queries:
        runs.append(("web", query, fleet_search("web", query, n)))
    primary = queries[-1]
    runs.append(("reddit", primary, fleet_search("reddit", primary, n)))
    runs.append(("github", primary, fleet_search("github", primary, n)))
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
                    rd = fleet_search_read(r["url"])
                    if rd.get("ok"):
                        top_read = {"url": r["url"], "title": r.get("title", ""),
                                    "comment_count": rd.get("comment_count", 0),
                                    "content": (rd.get("content") or "")[:1500],
                                    "comments": rd.get("comments", [])[:8]}
                    break
            break
    return runs, per_source, top_read


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
                extra = f" (star {r.get('stars')})"
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Prior-art research phase (wq#991).")
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
    print(f"[research_phase] project={project} queries={queries}", file=sys.stderr)

    runs, per_source, top_read = run_phase(project, queries, args.n)
    total = sum(per_source.values())

    if total == 0:
        # Fail-closed: write nothing. A header-only PRIOR_ART.md would be
        # non-empty and would wrongly satisfy the wq#1006 gate.
        errs = "; ".join(f"{k}:{e.get('error','?')}" for k, _q, e in runs if not e.get("ok"))
        print(f"[research_phase] ALL SOURCES RETURNED ZERO RESULTS — wrote nothing "
              f"(fail-closed). errors: {errs[:400]}", file=sys.stderr)
        print(json.dumps({"ok": False, "project": project, "total_results": 0,
                          "prior_art_written": False}))
        return 2

    findings_path = write_prior_art(rdir, project, queries, runs, per_source, top_read)
    print(f"[research_phase] wrote {findings_path} "
          f"({total} results across {sorted(per_source)})", file=sys.stderr)
    print(json.dumps({"ok": True, "project": project, "total_results": total,
                      "sources_ok": sorted(per_source.keys()),
                      "prior_art_file": findings_path}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
