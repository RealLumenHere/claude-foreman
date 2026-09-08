#!/usr/bin/env python3
"""render_report.py — wq#1492 backlog-sweep report from the sweep's own JSON.

    python3 harness/liveness_clear.py --scope all --json [--apply ...] > run.json
    python3 ops/wq1492-liveness-backlog-sweep/render_report.py run.json \
        --audit /home/lumen/foreman/harness/runtime/liveness_audit.jsonl > table.md

Read-only: queue.db opened ?mode=ro for the dep-met list; the audit jsonl is read for
the close-evidence section. Category is DERIVED from the probe name (deterministic),
never typed by hand; the no_tag_reasons.yaml row supplies the reason for kept tickets.
"""
import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REASONS = os.path.join(HERE, "no_tag_reasons.yaml")
WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")

CATEGORY = {"cred_alive": "cred", "balance_over": "cred",
            "repo_is": "repo", "pr_merged": "repo",
            "ts_key_expiry_disabled": "ts",
            "path_exists": "path", "file_contains": "path", "json_gte": "path",
            "service_active": "service", "node_healthy": "node",
            "ticket_done": "ticket-dep"}
ORDER = ("cred", "repo", "ts", "path", "service", "node", "ticket-dep", "unprobeable")


def load_reasons(path=REASONS):
    import yaml
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    return {str(k): v for k, v in (doc.get("reasons") or {}).items()}


def category(rec):
    probes = rec.get("probes") or []
    if not probes:
        return "unprobeable"
    cats = {CATEGORY.get(p["probe"], "path") for p in probes}
    return sorted(cats, key=ORDER.index)[0] if len(cats) == 1 else "/".join(sorted(cats, key=ORDER.index))


def dep_met_rows(recs, db=WQ_DB):
    """Blocked/open tickets whose deps= are ALL done: unblock candidates, never closes."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = []
    for r in recs:
        row = con.execute("SELECT deps, status FROM tasks WHERE id=?", (r["ticket"],)).fetchone()
        if not row or not (row[0] or "").strip():
            continue
        deps = [d.strip() for d in row[0].split(",") if d.strip()]
        st = {d: (con.execute("SELECT status FROM tasks WHERE id=?", (int(d),)).fetchone() or ["?"])[0]
              for d in deps if d.isdigit()}
        if st and all(v == "done" for v in st.values()):
            out.append((r["ticket"], row[1], ", ".join(f"wq#{d}" for d in st)))
    con.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_json")
    ap.add_argument("--audit", default=None)
    ap.add_argument("--db", default=WQ_DB)
    a = ap.parse_args(argv)
    recs = json.load(open(a.run_json))
    reasons = load_reasons()
    by_cat = {c: [] for c in ORDER}
    for r in recs:
        by_cat.setdefault(category(r), []).append(r)
    out = []
    counts = {c: len(v) for c, v in by_cat.items() if v}
    out.append("| category | tickets | SATISFIED | STILL_BLOCKED | UNKNOWN | NO_TAG | closed |")
    out.append("|---|---|---|---|---|---|---|")
    for c in ORDER:
        rows = by_cat.get(c) or []
        if not rows:
            continue
        vc = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("SATISFIED", "STILL_BLOCKED", "UNKNOWN", "NO_TAG")}
        out.append(f"| {c} | {len(rows)} | {vc['SATISFIED']} | {vc['STILL_BLOCKED']} | {vc['UNKNOWN']} | {vc['NO_TAG']} | "
                   f"{sum(1 for r in rows if r['action'] == 'closed')} |")
    out.append(f"| **total** | {sum(counts.values())} | | | | | {sum(1 for r in recs if r['action'] == 'closed')} |")
    out.append("")
    for c in ORDER:
        rows = by_cat.get(c) or []
        if not rows:
            continue
        out.append(f"### {c} ({len(rows)})")
        out.append("")
        out.append("| id | lane | prior status | probe | verdict | action | reason / detail |")
        out.append("|---|---|---|---|---|---|---|")
        for r in rows:
            probe = "<br>".join(f"{p['probe']}({p['args']})" for p in r["probes"]) or "—"
            detail = "<br>".join(f"{p['verdict']}: {p['detail']}" for p in r["probes"])
            if not r["probes"]:
                rr = reasons.get(str(r["ticket"]), {})
                detail = f"[{rr.get('cat', '?')}] {rr.get('why', 'NO REASON RECORDED')}"
            detail = detail.replace("|", "\\|")
            out.append(f"| {r['ticket']} | {r['lane']} | {r['prior_status']} | {probe} | {r['verdict']} | "
                       f"{r['action']} | {detail} |")
        out.append("")
    out.append("### dep-met unblock candidates (deps= all done; NOT closed — the work remains)")
    out.append("")
    out.append("| id | status | deps (all done) |")
    out.append("|---|---|---|")
    for tid, st, deps in dep_met_rows(recs, a.db):
        out.append(f"| {tid} | {st} | {deps} |")
    out.append("")
    if a.audit and os.path.exists(a.audit):
        out.append("### closes in liveness_audit.jsonl (this run's actor, action=closed)")
        out.append("")
        ids = {r["ticket"] for r in recs if r["action"] == "closed"}
        for line in open(a.audit):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("action") == "closed" and rec.get("ticket") in ids:
                probes = "; ".join(f"{p['probe']}({p['args']}) -> {p['verdict']}: {p['detail']}" for p in rec["probes"])
                out.append(f"- wq#{rec['ticket']} [{rec.get('lane')}/{rec.get('prior_status')} -> done] {rec['ts']} — {probes}")
        out.append("")
    unknown = [r for r in recs if r["verdict"] == "UNKNOWN"]
    out.append(f"### UNKNOWN flags for the orchestrator ({len(unknown)})")
    out.append("")
    for r in unknown:
        out.append(f"- wq#{r['ticket']} {r['action']}: " + "; ".join(f"{p['probe']}({p['args']}) -> {p['detail']}" for p in r["probes"]))
    if not unknown:
        out.append("- none")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
