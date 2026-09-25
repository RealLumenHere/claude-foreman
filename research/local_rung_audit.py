#!/usr/bin/env python3
"""wq#2576: local LLM rung audit — per-model request volume and outcomes, 30d.

Two read-only sources:
  1. queue.db (tickets by model, status, archetype, block reason, verify-pass tickets)
  2. _runs/wq*/attempt-N.json (one row per claude session fworker ran: resolved model,
     gateway backend, classification, turns, elapsed). `turns` is the number of model
     calls the session made, so its sum per model is the fleet-worker share of LiteLLM
     request volume for that model.

Usage: research/local_rung_audit.py [--days 30] [--json]
"""
import argparse
import collections
import glob
import json
import os
import re
import sqlite3
import time

QUEUE_DB = "/home/lumen/agents/work-queue/queue.db"
RUNS = "/home/lumen/foreman/_runs"
LOCAL = ("hathor-llama8b", "local-worker", "qwen36-local", "nephthys-qwen14b")

BLOCK_BUCKETS = [
    ("RUNG_FAULT", "RUNG_FAULT"), ("FALSE_COMPLETE", "FALSE_COMPLETE"),
    ("SPAWN_LOOP", "SPAWN_LOOP"), ("EVIDENCE_GATE", "evidence gate"), ("HANG", "HANG"),
    ("CRASH", "CRASH"), ("KILLED", "KILLED"), ("CITED_PATH_UNREACHABLE", "CITED_PATH"),
    ("CAPABILITY_DENIED", "CAPABILITY"), ("TURNS", "TURNS"), ("FRANK", "FRANK"),
]


def bucket(reason):
    reason = reason or ""
    for name, needle in BLOCK_BUCKETS:
        if needle in reason:
            return name
    return "OTHER"


def queue_stats(db, days):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    since = f"date('now','-{int(days)} day')"
    q = lambda sql: con.execute(sql).fetchall()
    local_in = ",".join(f"'{m}'" for m in LOCAL)
    out = {}
    out["tickets_by_model"] = q(
        f"select model, count(*) from tasks where created_at >= {since} "
        f"and model is not null and model != 'None' group by model order by 2 desc")
    out["local_by_status"] = q(
        f"select status, count(*) from tasks where model in ({local_in}) "
        f"and created_at >= {since} group by status")
    out["local_by_archetype_status"] = q(
        f"select coalesce(archetype,'-'), status, count(*) from tasks "
        f"where model in ({local_in}) and created_at >= {since} group by 1,2 order by 1,2")
    blocked = q(f"select id, archetype, block_reason, attempt from tasks "
                f"where model in ({local_in}) and status='blocked' and created_at >= {since}")
    b = collections.Counter(bucket(r[2]) for r in blocked)
    out["local_block_buckets"] = b.most_common()
    # verify-pass tickets filed against local-authored parents: the sonnet-5 cycles local
    # work costs even when it closes (verify_pass.local_author_always).
    parents = {r[0]: r for r in q(
        f"select id, model, status from tasks where model in ({local_in}) "
        f"and created_at >= {since} and archetype != 'verify-pass'")}
    vrows = q(f"select id, model, status, title, coalesce(review_verdict,''), "
              f"coalesce(notes,'') from tasks where title like '[verify] wq#%' "
              f"and created_at >= {since}")
    verify = []
    for vid, vmodel, vstatus, title, verdict, notes in vrows:
        m = re.match(r"\[verify\] wq#(\d+)", title)
        if m and int(m.group(1)) in parents:
            verdict = verdict or ("CONTRADICT" if "CONTRADICT" in notes.upper() else
                                  "CONFIRM" if "CONFIRM" in notes.upper() else "?")
            verify.append((vid, int(m.group(1)), vmodel, vstatus, verdict))
    out["verify_of_local_parents"] = verify
    out["local_parent_reopened"] = q(
        f"select count(*), sum(reopened_count) from tasks where model in ({local_in}) "
        f"and created_at >= {since} and reopened_count > 0")
    con.close()
    return out


def runs_stats(runs, days):
    cutoff = time.time() - days * 86400
    per_model = collections.defaultdict(lambda: {
        "attempts": 0, "turns": 0, "elapsed_s": 0.0, "tickets": set(),
        "classes": collections.Counter(), "backends": collections.Counter(),
        "fallbacks": 0, "cost_usd": 0.0})
    for path in glob.glob(os.path.join(runs, "wq*", "attempt-*.json")):
        try:
            with open(path) as f:
                a = json.load(f)
        except (OSError, ValueError):
            continue
        if (a.get("started_at") or 0) < cutoff:
            continue
        m = a.get("model") or "?"
        s = per_model[m]
        s["attempts"] += 1
        s["turns"] += int(a.get("turns") or 0)
        s["elapsed_s"] += float(a.get("elapsed_s") or 0)
        s["tickets"].add(a.get("ticket"))
        s["classes"][a.get("classification") or "?"] += 1
        s["backends"][a.get("gateway_backend") or "-"] += 1
        s["fallbacks"] += 1 if a.get("gateway_fallback") else 0
        s["cost_usd"] += float(a.get("total_cost_usd") or 0)
    rows = []
    for m, s in per_model.items():
        rows.append({"model": m, "attempts": s["attempts"], "tickets": len(s["tickets"]),
                     "turns": s["turns"], "hours": round(s["elapsed_s"] / 3600, 1),
                     "cost_usd": round(s["cost_usd"], 2), "fallbacks": s["fallbacks"],
                     "classes": dict(s["classes"].most_common()),
                     "backends": dict(s["backends"].most_common(4))})
    rows.sort(key=lambda r: -r["turns"])
    return rows


def local_done_closers(db, runs, days):
    """For each local-model ticket that reached done: which session (if any) closed it.
    'no fworker session' means it was closed outside the dispatcher (orchestrator, a
    human, or a duplicate/superseded sweep), not by a local model."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    local_in = ",".join(f"'{m}'" for m in LOCAL)
    ids = [r[0] for r in con.execute(
        f"select id from tasks where model in ({local_in}) and status='done' "
        f"and created_at >= date('now','-{int(days)} day')")]
    con.close()
    out = collections.Counter()
    detail = {}
    for tid in ids:
        atts = []
        for path in sorted(glob.glob(os.path.join(runs, f"wq{tid}", "attempt-*.json"))):
            try:
                with open(path) as f:
                    a = json.load(f)
                atts.append((a.get("model") or "?", a.get("classification") or "?"))
            except (OSError, ValueError):
                continue
        wins = [m for m, c in atts if c in ("SUCCESS", "DEGRADED_SUCCESS")]
        key = f"closed by {wins[-1]}" if wins else ("no fworker session" if not atts
                                                    else "no successful session")
        out[key] += 1
        detail[tid] = atts
    return out.most_common(), detail


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    closers, closer_detail = local_done_closers(QUEUE_DB, RUNS, args.days)
    data = {"queue": queue_stats(QUEUE_DB, args.days), "runs": runs_stats(RUNS, args.days),
            "local_done_closers": closers, "local_done_attempts": closer_detail}
    if args.json:
        print(json.dumps(data, indent=1, default=str))
        return
    qs = data["queue"]
    print(f"== tickets by model ({args.days}d)")
    for m, n in qs["tickets_by_model"]:
        print(f"  {m:24} {n}")
    print("== local by status:", dict(qs["local_by_status"]))
    print("== local by archetype/status:")
    for a, st, n in qs["local_by_archetype_status"]:
        print(f"  {a:22} {st:8} {n}")
    print("== local block buckets:", qs["local_block_buckets"])
    v = qs["verify_of_local_parents"]
    print(f"== verify tickets on local parents: {len(v)} "
          f"verdicts={dict(collections.Counter(r[4] for r in v))} "
          f"models={dict(collections.Counter(r[2] for r in v))}")
    print("== local parents reopened (tickets, total reopens):", qs["local_parent_reopened"])
    print("== local-model tickets marked done, closed by:", data["local_done_closers"])
    print(f"== fworker sessions by resolved model ({args.days}d; turns = model calls)")
    for r in data["runs"]:
        top = ",".join(f"{k}:{n}" for k, n in list(r["classes"].items())[:4])
        print(f"  {r['model']:22} att={r['attempts']:4} tix={r['tickets']:4} "
              f"turns={r['turns']:6} h={r['hours']:6} fb={r['fallbacks']:3} {top}")


if __name__ == "__main__":
    main()
