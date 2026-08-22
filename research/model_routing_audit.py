#!/usr/bin/env python3
"""model_routing_audit.py — wq#750 CAL-1+ measurement lever.

Re-runnable evidence generator for research/MODEL_ROUTING_AUDIT.md. Read-only:
it opens every source with mode=ro / plain reads and never dispatches a model.

Sources
  A. /home/lumen/foreman/_runs/*/attempt-*.json  — real foreman dispatch journal
     (model, effort, archetype, turns, elapsed, cpu, outcome). This is the only
     per-launch ground truth we have; ~/.claude/projects transcripts are
     guard-denied, so token counts are NOT available and burn is reported in
     turns / wall-clock / cpu instead.
  B. /home/lumen/agents/work-queue/queue.db — every ticket ever filed, used to
     replay orchestrator/router.py classify_archetype() over real text.
  C. /home/lumen/messenger/data/messenger.db — real user turns, used to measure
     how often agent_bridge._RE_TRIGGER_PAT actually escalates chat to the RE tier.

Usage:  python3 research/model_routing_audit.py [section ...]
        sections: burn | classify | messenger | all   (default: all)
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sqlite3
import statistics as st
import sys

RUNS = "/home/lumen/foreman/_runs/*/attempt-*.json"
QUEUE_DB = "/home/lumen/agents/work-queue/queue.db"
MESSENGER_DB = "/home/lumen/messenger/data/messenger.db"
ROUTER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "orchestrator")
BRIDGE = "/home/lumen/messenger/agent_bridge.py"


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _med(v):
    return round(st.median(v), 1) if v else None


# ---- A. measured burn matrix ---------------------------------------------------------

def load_attempts():
    out = []
    for f in glob.glob(RUNS):
        try:
            with open(f) as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda d: d.get("started_at") or 0)
    return out


def burn(rows):
    print(f"== A. measured burn matrix (n={len(rows)} launches)")
    g = collections.defaultdict(list)
    for r in rows:
        tup = r["model"].replace("claude-", "") + "@" + str(r.get("effort"))
        g[(r["archetype"], tup)].append(r)
    hdr = ("archetype", "tuple", "n", "succ", "capped", "turns_med", "turns_max",
           "elapsed_med_s", "cpu_med_s")
    print("{:22}{:16}{:>4}{:>6}{:>8}{:>11}{:>10}{:>15}{:>11}".format(*hdr))
    for k in sorted(g):
        rs = g[k]
        n = len(rs)
        c = collections.Counter(r["classification"] for r in rs)
        turns = [r["turns"] for r in rs if r.get("turns") is not None]
        capped = sum(1 for r in rs if r.get("turns", 0) >= r.get("max_turns", 1 << 30))
        cpu = [r["cpu_s"] for r in rs if r.get("cpu_s") is not None]
        print("{:22}{:16}{:>4}{:>6}{:>8}{:>11}{:>10}{:>15}{:>11}".format(
            k[0], k[1], n, c["SUCCESS"], capped, str(_med(turns)),
            str(max(turns) if turns else None),
            str(_med([r["elapsed_s"] for r in rs])), str(_med(cpu))))
    print()
    print("  fleet totals:", collections.Counter(
        r["model"].replace("claude-", "") for r in rows).most_common())
    print("  outcomes:    ", collections.Counter(
        r["classification"] for r in rows).most_common())
    capped = sum(1 for r in rows if r.get("turns", 0) >= r.get("max_turns", 1 << 30))
    print(f"  turn-capped: {capped}/{len(rows)} ({100*capped/len(rows):.0f}%)")
    print("  model_downgraded:", sum(1 for r in rows if r.get("model_downgraded")))
    print("  saw_limit:", sum(1 for r in rows if r.get("saw_limit")))
    print("  haiku launches:", sum(1 for r in rows if "haiku" in r["model"]))
    print("  local-rung launches:",
          sum(1 for r in rows if r["model"] in ("deepseek-v4-flash", "qwen36-local")))
    print()


# ---- B. classifier replay ------------------------------------------------------------

CHEAP = {"mechanical-edit", "probe-build"}
COST_RANK = {  # rough $ order of the primary rung each archetype routes to
    "mechanical-edit": 0, "probe-build": 1, "verify-pass": 2,
    "standard-engineering": 3, "research-synthesis": 4,
    "rca-investigation": 5, "novel-design": 6, "long-horizon-campaign": 6,
}


def classify(_router):
    with _ro(QUEUE_DB) as c:
        rows = list(c.execute("select id,lane,title,detail from tasks"))
    print(f"== B. classify_archetype() replayed over every filed ticket (n={len(rows)})")
    dist = collections.Counter()
    no_signal = 0
    outranked = collections.Counter()
    per_signal = collections.Counter()
    for _id, lane, title, detail in rows:
        text = f"{title or ''}\n{detail or ''}"
        arch, sig = _router.classify_archetype(title or "", detail or "", lane or "")
        dist[arch] += 1
        if not sig:
            no_signal += 1
        hits = set()
        for a, pats in _router._RUBRIC:
            for p in pats:
                if re.search(p, text, re.IGNORECASE):
                    hits.add(a)
                    per_signal[(a, p)] += 1
        if (hits & CHEAP) and arch not in CHEAP:
            outranked[arch] += 1
    for k, v in dist.most_common():
        print(f"  {k:24}{v:5}  {100*v/len(rows):5.1f}%")
    print(f"  no-signal fallthrough -> standard-engineering: {no_signal} "
          f"({100*no_signal/len(rows):.0f}%)")
    print(f"  cheap signal present but outranked: {sum(outranked.values())} "
          f"-> {outranked.most_common()}")
    print("  top firing signals:")
    for (a, p), n in per_signal.most_common(12):
        print(f"    {n:5}  {a:22}{p}")
    print()
    return dist


def classify_counterfactual(_router):
    """What if the first-wins order were cost-ascending instead of _RUBRIC order?"""
    with _ro(QUEUE_DB) as c:
        rows = list(c.execute("select id,lane,title,detail from tasks"))
    now = collections.Counter()
    cheapest = collections.Counter()
    moved = 0
    for _id, lane, title, detail in rows:
        text = f"{title or ''}\n{detail or ''}"
        arch, _ = _router.classify_archetype(title or "", detail or "", lane or "")
        now[arch] += 1
        hits = [a for a, pats in _router._RUBRIC
                if any(re.search(p, text, re.IGNORECASE) for p in pats)]
        alt = min(hits, key=lambda a: COST_RANK.get(a, 9)) if hits else "standard-engineering"
        cheapest[alt] += 1
        if COST_RANK.get(alt, 9) < COST_RANK.get(arch, 9):
            moved += 1
    print("== B2. counterfactual: cheapest-hit-wins instead of _RUBRIC-order-wins")
    print(f"  tickets that would route strictly cheaper: {moved} "
          f"({100*moved/len(rows):.0f}% of {len(rows)})")
    for k in sorted(set(now) | set(cheapest), key=lambda a: COST_RANK.get(a, 9)):
        print(f"  {k:24} now={now[k]:5}   cheapest-wins={cheapest[k]:5}")
    print()


# ---- C. messenger RE-tier heuristic --------------------------------------------------

def _re_pattern():
    """Extract _RE_TRIGGER_PAT from agent_bridge.py without importing it."""
    src = open(BRIDGE).read()
    m = re.search(r"_RE_TRIGGER_PAT = re\.compile\(\s*(.*?)\s*re\.IGNORECASE,\s*\)",
                  src, re.S)
    body = "".join(re.findall(r'r"([^"]*)"', m.group(1)))
    return re.compile(body, re.IGNORECASE)


def messenger():
    pat = _re_pattern()
    with _ro(MESSENGER_DB) as c:
        rows = list(c.execute(
            "select text, time from messages where is_user=1 order by time"))
    users = [(r[0] or "", r[1] or "") for r in rows]
    fires = [u for u, _ in users if pat.search(u)]
    manual = [u for u, _ in users if u.lstrip()[:8].lower().startswith(("[pro]", "[re]"))]
    local = [u for u, _ in users if u.lstrip()[:8].lower().startswith(("[local]", "[q]", "[m:"))]
    print(f"== C. messenger RE-escalation heuristic on real traffic (n={len(users)} user turns)")
    print(f"  span: {users[0][1]} .. {users[-1][1]}")
    print(f"  heuristic fires : {len(fires)} ({100*len(fires)/len(users):.1f}%) "
          f"-> {collections.Counter(pat.search(u).group(0).lower() for u in fires).most_common(6)}")
    print(f"  explicit [pro]/[re] : {len(manual)}")
    print(f"  explicit local/picker: {len(local)}")
    byday = collections.Counter(str(t)[:10] for _, t in users)
    last = sorted(byday)[-14:]
    print(f"  turns/day (last 14 active days): {[byday[d] for d in last]}")
    print(f"  median turns/active day: {_med(list(byday.values()))}")
    print()


def sole_signal(_router):
    """How much of each expensive archetype rests on ONE over-broad token?

    A ticket is 'sole-signal' for pattern p when p is the only pattern of the
    winning archetype that fires. Drop p and the ticket reclassifies.
    """
    with _ro(QUEUE_DB) as c:
        rows = list(c.execute("select id,lane,title,detail from tasks"))
    pats = dict(_router._RUBRIC)
    sole = collections.Counter()
    total = collections.Counter()
    for _id, lane, title, detail in rows:
        text = f"{title or ''}\n{detail or ''}"
        arch, _ = _router.classify_archetype(title or "", detail or "", lane or "")
        total[arch] += 1
        fired = [p for p in pats.get(arch, [])
                 if re.search(p, text, re.IGNORECASE)]
        if len(fired) == 1:
            sole[(arch, fired[0])] += 1
    print("== B3. sole-signal load: winning archetype rests on exactly one pattern")
    for (a, p), n in sole.most_common(14):
        print(f"  {n:5}  ({100*n/max(1,total[a]):4.0f}% of {a:22}) {p}")
    print()


def main():
    want = set(sys.argv[1:]) or {"all"}
    sys.path.insert(0, os.path.abspath(ROUTER_DIR))
    if {"all", "burn"} & want:
        burn(load_attempts())
    if {"all", "classify"} & want:
        import router
        classify(router)
        classify_counterfactual(router)
        sole_signal(router)
    if {"all", "messenger"} & want:
        messenger()


if __name__ == "__main__":
    main()
