#!/usr/bin/env python3
"""Why each closed fact is or is not in the dead-value index (wq#1609 evidence)."""
import collections
import importlib.util
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "lcs", os.path.join(HERE, "lumen_contradiction_scan.py"))
lcs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lcs)

db = sys.argv[1] if len(sys.argv) > 1 else lcs.MINERVA_DB
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
closed = conn.execute(
    "select subject, predicate, value, fact_class from state_facts "
    "where valid_to is not null").fetchall()
open_now = {
    (s, p): v for s, p, v in conn.execute(
        "select subject, predicate, value from state_facts where valid_to is null")
}

reasons = collections.Counter()
for subj, pred, val, fclass in closed:
    if pred in lcs.VOLATILE_PREDICATES:
        reasons["skipped: volatile predicate"] += 1
    elif fclass == "narrative":
        reasons["skipped: fact_class=narrative"] += 1
    elif len(val) > lcs.MAX_VALUE_LEN:
        reasons["skipped: prose value over 60 chars"] += 1
    elif not lcs.TOKEN_RE.fullmatch(val):
        reasons["skipped: multi-token value"] += 1
    else:
        current = open_now.get((subj, pred))
        if current is not None and lcs.boundary_re(val).search(current):
            reasons["skipped: value still live in the current value"] += 1
        else:
            reasons["INDEXED"] += 1

ip_only = sum(1 for _, _, v, _ in closed if lcs.SELF_IDENTIFYING[0].fullmatch(v))
print(f"closed facts: {len(closed)}")
print(f"old IP-only filter reach: {ip_only}")
for reason, n in reasons.most_common():
    print(f"{n:5}  {reason}")
