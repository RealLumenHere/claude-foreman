#!/usr/bin/env python3
"""liveness_sweep.py — Liveness Gate Slice 2, READ-ONLY sweep (LIVENESS_GATE.md §8).

Runs every open frank-gated ticket's `Liveness:` probes (or its ledger binding) and
reports STALE vs STILL_BLOCKED vs UNKNOWN vs NO_TAG. Zero writes: no wq calls, no
notes, no status changes, no ledger refresh. Slice 3 (auto-clear) is NOT here.

    python3 liveness_sweep.py            # table
    python3 liveness_sweep.py --json     # machine output
    python3 liveness_sweep.py --ids 902 1101   # subset

Frank-gated predicate = the dispatcher's own: HUMAN_LANES (frank-* + user lanes),
frank_gate_reason() (title marker / decision gate pending / wq gate unmet). One
predicate, imported, not copied.
"""
import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "dispatcher"))
import capability  # noqa: E402
import liveness  # noqa: E402

WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
LEDGER = os.environ.get("FOREMAN_LIVENESS_LEDGER", os.path.join(HERE, "liveness_ledger.yaml"))
OPEN_STATUSES = ("open", "blocked", "pending_review", "frank_gated")

LABEL = {liveness.SATISFIED: "STALE", liveness.STILL_BLOCKED: "STILL_BLOCKED",
         liveness.UNKNOWN: "UNKNOWN", capability.LIVENESS_NO_TAG: "NO_TAG"}


def load_bindings(path=LEDGER):
    try:
        import yaml
        with open(path) as f:
            doc = yaml.safe_load(f) or {}
    except Exception:
        return {}
    return {str(k): str(v) for k, v in (doc.get("bindings") or {}).items()}


SCOPES = ("frank", "all")


def _backlog_rows(db=WQ_DB, ids=None):
    """Read-only select of every ticket in OPEN_STATUSES (optionally a subset of ids)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = f"SELECT * FROM tasks WHERE status IN ({','.join('?' * len(OPEN_STATUSES))})"
    args = list(OPEN_STATUSES)
    if ids:
        q += f" AND id IN ({','.join('?' * len(ids))})"
        args += list(ids)
    rows = [dict(r) for r in con.execute(q + " ORDER BY id", args)]
    con.close()
    return rows


def _gate_why(t):
    import foreman_dispatcher as fd  # import is side-effect free (verified 2026-09-07)
    if (t.get("lane") or "") in fd.HUMAN_LANES:
        return f"lane {t['lane']}"
    return fd.frank_gate_reason(t)


def frank_gated_tickets(db=WQ_DB, ids=None):
    """Read-only select of open tickets the dispatcher would classify as Frank's."""
    out = []
    for t in _backlog_rows(db, ids):
        why = _gate_why(t)
        if why:
            t["_gate_why"] = why
            out.append(t)
    return out


def backlog_tickets(db=WQ_DB, ids=None):
    """wq#1492 --scope all: every ticket in OPEN_STATUSES, frank-gated or not.
    `_gate_why` is the dispatcher's reason when it has one, else 'scope:all'."""
    out = []
    for t in _backlog_rows(db, ids):
        t["_gate_why"] = _gate_why(t) or "scope:all"
        out.append(t)
    return out


def tickets_in_scope(scope="frank", db=WQ_DB, ids=None):
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    return backlog_tickets(db, ids) if scope == "all" else frank_gated_tickets(db, ids)


def sweep(ids=None, db=WQ_DB, ledger=LEDGER, scope="frank"):
    bindings = load_bindings(ledger)
    report = []
    for t in tickets_in_scope(scope, db, ids):
        verdict, results = capability.liveness_verdict(t, bindings)
        report.append({
            "id": t["id"], "status": t["status"], "lane": t["lane"],
            "title": (t.get("title") or "")[:70], "gate": t["_gate_why"],
            "verdict": LABEL[verdict],
            "source": ("ticket" if "Liveness:" in (t.get("detail") or "")
                       else ("ledger" if str(t["id"]) in bindings else "none")),
            "probes": [{"probe": p, "args": a, "verdict": v, "detail": d}
                       for p, a, v, d in results],
        })
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ids", nargs="*", type=int)
    ap.add_argument("--db", default=WQ_DB)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--scope", choices=SCOPES, default="frank",
                    help="frank (default: the dispatcher's frank-gated predicate) or all "
                         "(every open|blocked|frank_gated|pending_review ticket, wq#1492)")
    a = ap.parse_args(argv)
    rep = sweep(a.ids, a.db, a.ledger, scope=a.scope)
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    counts = {}
    for r in rep:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    what = "frank-gated ticket(s)" if a.scope == "frank" else "backlog ticket(s) [scope=all]"
    print(f"liveness sweep (READ-ONLY, no state change): {len(rep)} {what} "
          + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for r in rep:
        print(f"  wq#{r['id']:<5} {r['verdict']:<13} [{r['lane']}/{r['status']}] {r['title']}")
        for p in r["probes"]:
            print(f"           {p['verdict']:<13} {p['probe']}({p['args']}) -> {p['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
