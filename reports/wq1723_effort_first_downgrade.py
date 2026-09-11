#!/usr/bin/env python3
"""wq#1723 evidence: is the router's effort-first downgrade worth keeping?

Answers the ticket's criterion — "does lower effort measurably save pool % per ticket
vs the rework cost" — from the two corpora that can answer it:

  _runs/wq<ID>/attempt-N.json   model, effort, archetype, classification, turns, cost
  _runs/wq<ID>/usage-N.json     5h/7d pool used_percentage at run start and at run exit
  queue.db tasks.reopened_count the wq#841 structural rework signal

Prints four sections; reports/wq1723_effort_first_downgrade.md is the written-up result.
Read-only: opens the queue db with mode=ro and never touches _runs.
"""
import glob
import json
import os
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime

RUNS = "/home/lumen/foreman/_runs"
WQ_DB = "/home/lumen/agents/work-queue/queue.db"
SUCCESS = "SUCCESS"
# resets_at carries sub-second poll jitter for the SAME window ('...15:29:59.58' vs
# '...15:30:00.05'). Comparing it exactly classifies all 180 real deltas in the corpus as
# window rolls and reports a flat zero everywhere, so allow a tolerance.
WINDOW_TOL_S = 120


def same_window(a, b):
    if not a or not b:
        return False
    try:
        return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) <= WINDOW_TOL_S
    except ValueError:
        return a == b


def load_rows():
    """One row per worker attempt, joining its attempt-N.json to its usage-N.json."""
    rows = []
    for ap in glob.glob(os.path.join(RUNS, "wq*", "attempt-*.json")):
        try:
            a = json.load(open(ap))
        except (OSError, ValueError):
            continue
        n = os.path.basename(ap).split("-")[1].split(".")[0]
        up = os.path.join(os.path.dirname(ap), "usage-%s.json" % n)
        try:
            u = json.load(open(up)) if os.path.exists(up) else {}
        except (OSError, ValueError):
            u = {}

        def delta(pool):
            s = (u.get("rate_limits_start") or {}).get(pool) or {}
            e = (u.get("rate_limits_exit") or {}).get(pool) or {}
            if s.get("used_percentage") is None or e.get("used_percentage") is None:
                return None
            if not same_window(s.get("resets_at"), e.get("resets_at")):
                return None  # the window reset mid-run; the delta means nothing
            return e["used_percentage"] - s["used_percentage"]

        rows.append(dict(
            ticket=a.get("ticket"), attempt=a.get("attempt"), model=a.get("model"),
            effort=a.get("effort"), arch=a.get("archetype"), cls=a.get("classification"),
            turns=a.get("turns"), cost=a.get("total_cost_usd"), elapsed=a.get("elapsed_s"),
            d5=delta("five_hour"), d7=delta("seven_day"),
        ))
    return rows


def med(xs):
    return st.median(xs) if xs else float("nan")


def mean(xs):
    return st.mean(xs) if xs else float("nan")


def pearson(xy):
    if len(xy) < 3:
        return float("nan")
    xs, ys = zip(*xy)
    mx, my = mean(xs), mean(ys)
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return sum((x - mx) * (y - my) for x, y in xy) / den if den else float("nan")


def row_line(label, v):
    d5 = [r["d5"] for r in v if r["d5"] is not None]
    cl = [r["cls"] for r in v if r["cls"]]
    ok = sum(1 for c in cl if c == SUCCESS)
    print("  %-40s %4d %5d %8.2f %8.3f %8.1f %7.1f"
          % (label, len(v), len(d5), mean(d5),
             med([r["cost"] for r in v if r["cost"]]), med([r["turns"] for r in v if r["turns"]]),
             100.0 * ok / len(cl) if cl else float("nan")))


HEAD = "  %-40s %4s %5s %8s %8s %8s %7s" % ("bucket", "n", "n5h", "mean5h%", "medCost", "medTurns", "succ%")


def main():
    rows = load_rows()
    print("attempts in corpus: %d   (classifications: %s)"
          % (len(rows), ", ".join("%s=%d" % kv for kv in Counter(r["cls"] for r in rows).most_common(5))))

    print("\n== 1. headline: every (model, effort) bucket ==")
    print(HEAD)
    by = defaultdict(list)
    for r in rows:
        by[(r["model"], r["effort"])].append(r)
    for k in sorted(by, key=str):
        if len(by[k]) >= 5:
            row_line("%s @ %s" % (str(k[0]).replace("claude-", ""), k[1]), by[k])

    print("\n== 2. archetype-controlled: same model, adjacent effort ==")
    print(HEAD)
    byame = defaultdict(list)
    for r in rows:
        byame[(r["model"], r["arch"], r["effort"])].append(r)
    for m, a in sorted({(m, a) for m, a, _ in byame}, key=str):
        present = [(e, byame[(m, a, e)]) for e in ("low", "medium", "high", "xhigh")
                   if len(byame.get((m, a, e), [])) >= 5]
        if len(present) >= 2:
            for e, v in present:
                row_line("%s / %s @ %s" % (str(m).replace("claude-", ""), a, e), v)

    print("\n== 3. is a run's 5h pool delta attributable to that run? ==")
    cloud = [r for r in rows if r["d5"] is not None and "sonnet" in str(r["model"])]
    print("  sonnet-5 attempts with a same-window 5h delta: %d" % len(cloud))
    for name, key in (("elapsed_s", "elapsed"), ("total_cost_usd", "cost"), ("turns", "turns")):
        xy = [(r[key], r["d5"]) for r in cloud if r[key]]
        print("  corr(5h delta, %-15s) = %+.3f   (n=%d)" % (name, pearson(xy), len(xy)))

    print("\n== 4. rework: attempts and reopens per ticket, by the effort it ran at ==")
    byt = defaultdict(list)
    for r in rows:
        if r["ticket"]:
            byt[r["ticket"]].append(r)
    con = sqlite3.connect("file:%s?mode=ro" % WQ_DB, uri=True, timeout=30)
    reopened = {t: (c or 0) for t, c in con.execute("SELECT id, reopened_count FROM tasks")}
    con.close()
    print("  %-8s %8s %9s %10s %11s %12s"
          % ("effort", "tickets", "meanAtt", "multiAtt%", "meanReopen", "reopened>0%"))
    for e in ("low", "medium", "high", "xhigh"):
        # one effort and one model only, so a ticket that changed tuple mid-life is excluded
        ts = [(t, len(v)) for t, v in byt.items()
              if {x["effort"] for x in v if x["effort"]} == {e}
              and {x["model"] for x in v if x["model"]} == {"claude-sonnet-5"}]
        if not ts:
            continue
        atts = [n for _, n in ts]
        ro = [reopened.get(t, 0) for t, _ in ts]
        print("  %-8s %8d %9.2f %9.1f%% %11.2f %11.1f%%"
              % (e, len(ts), mean(atts), 100.0 * sum(1 for n in atts if n > 1) / len(atts),
                 mean(ro), 100.0 * sum(1 for c in ro if c) / len(ro)))


if __name__ == "__main__":
    main()
