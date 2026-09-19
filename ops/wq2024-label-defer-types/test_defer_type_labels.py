#!/usr/bin/env python3
"""test_defer_type_labels.py — wq#2024 acceptance replay. Pure-function tests for
defer_type_labels.classify_defer_type + an end-to-end backfill test against a synthetic
sqlite fixture matching the real `proposals` schema (columns per
ops/wq1500-minerva-promoter/test_minerva_promote.py and
ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py's fixtures —
this repo cannot reach the live minerva.db or its tests/ dir, see README.md).

Run: python3 ops/wq2024-label-defer-types/test_defer_type_labels.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import defer_type_labels as dtl
import backfill_defer_labels as bdl

FAILS = []
N = [0]


def check(cond, what):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


# ---- classifier: one real-shaped example per label, straight from the ticket's own
# reason-cluster phrasing -------------------------------------------------------------

EXAMPLES = {
    "defer-theoretical": [
        "Paper-focused; no reference implementation exists yet.",
        "Promising but unproven in production — needs a real deployment first.",
        "Not a ready-to-use tool, requires significant adaptation to fit our stack.",
        "Research-focused benchmark suite, not something we can install today.",
    ],
    "defer-needs-hardware": [
        "Infeasible on this hardware: no dedicated GPU on the head node.",
        "Requires AVX-512 which this Broadwell Xeon does not support.",
        "Insufficient RAM to hold the full checkpoint in memory.",
    ],
    "defer-needs-decision": [
        "Needs a design decision from Frank on which lane owns this.",
        "This is Frank's call: pick the retention policy before we build it.",
        "Waiting on a human judgement call about scope before proceeding.",
    ],
    "defer-dep-blocked": [
        "Blocked on wq#1560 landing the fleet-exec grant fix first.",
        "Depends on the pilot lane module, which has not yet landed.",
        "Waiting for another proposal to ship the shared schema first.",
    ],
    "defer-empirical": [
        "Needs a benchmark to decide between the two caching strategies.",
        "Run an A/B test comparing latency before committing either way.",
        "This needs a pilot to decide whether the win is real.",
    ],
    "defer-info-gap": [
        "Uncertain whether the upstream API supports streaming responses.",
        "Missing info on licensing terms before we can adopt this.",
        "Unclear if this conflicts with the existing dedup gate.",
    ],
    "defer-duplicate-suspect": [
        "Looks like a duplicate of an existing proposal already in the queue.",
        "This overlaps with existing work on the memory consolidation path.",
        "Same as proposal 9f1c — likely redundant with it.",
    ],
}

for label, reasons in EXAMPLES.items():
    for r in reasons:
        got = dtl.classify_defer_type(r)
        check(got == label, f"classify({r!r}) -> {got!r} (want {label!r})")

check(dtl.classify_defer_type("") is None, "empty reason -> None (unmatched, not guessed)")
check(dtl.classify_defer_type(None) is None, "None reason -> None (unmatched, not guessed)")
check(dtl.classify_defer_type("the sky is blue today") is None,
      "unrelated text -> None (unmatched, not guessed)")

# Priority: a reason that is BOTH dep-blocked-shaped and theoretical-shaped must not be
# swallowed by the broad 'theoretical' bucket — dep-blocked is checked first.
mixed = "Academic paper, but really we are just blocked on wq#1600 landing first."
check(dtl.classify_defer_type(mixed) == "defer-dep-blocked",
      f"mixed dep-blocked+theoretical -> defer-dep-blocked, got {dtl.classify_defer_type(mixed)!r}")

# Priority: duplicate-suspect must win over theoretical when both phrasings appear,
# because it carries the ticket's VERIFY note and must never be silently absorbed.
mixed2 = "Promising but unproven research direction, and also looks like a duplicate of existing work."
check(dtl.classify_defer_type(mixed2) == "defer-duplicate-suspect",
      f"mixed duplicate+theoretical -> defer-duplicate-suspect, got {dtl.classify_defer_type(mixed2)!r}")

check(set(dtl.LABELS) == {
    "defer-theoretical", "defer-needs-hardware", "defer-needs-decision",
    "defer-dep-blocked", "defer-empirical", "defer-info-gap", "defer-duplicate-suspect",
}, "LABELS matches exactly the 7 labels the ticket names")

# ---- backfill: synthetic fixture matching the real proposals schema -----------------

TMP = Path(tempfile.mkdtemp(prefix="wq2024-defer-labels-"))
DB = str(TMP / "minerva.db")

SCHEMA = """CREATE TABLE proposals (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT,
    source_intel_id TEXT, current_state TEXT, proposed_change TEXT, effort TEXT,
    impact TEXT, risk TEXT, rollback TEXT, status TEXT DEFAULT 'pending',
    eval_result TEXT, eval_passed INTEGER DEFAULT 0, decision_reason TEXT,
    created_at TEXT, decided_at TEXT, deployed_at TEXT)"""

ROWS = [
    ("p1", "defer", "Needs a design decision from Frank on retention policy."),
    ("p2", "defer", "Paper-focused; promising but unproven in production."),
    ("p3", "defer", "Infeasible on this hardware: no dedicated GPU."),
    ("p4", "defer", "Blocked on wq#1560 landing first."),
    ("p5", "accept", "n/a — not a defer, must be left untouched"),
    ("p6", "defer", "the sky is blue today"),  # unmatched on purpose
]


def seed():
    c = sqlite3.connect(DB)
    c.execute(SCHEMA)
    for pid, status, reason in ROWS:
        c.execute(
            "INSERT INTO proposals (id, title, status, decision_reason, created_at) "
            "VALUES (?, ?, ?, ?, '2026-09-18T00:00:00')",
            (pid, f"proposal {pid}", status, reason),
        )
    c.commit()
    c.close()


seed()
result = bdl.backfill(DB)
check(result["labeled"] == 4, f"backfill labels exactly the 4 matched defer rows, got {result['labeled']}")
check(result["unmatched"] == ["p6"], f"p6 reported unmatched, got {result['unmatched']}")

c = sqlite3.connect(DB)
cols = [r[1] for r in c.execute("PRAGMA table_info(proposals)")]
check("defer_type_label" in cols, "additive column defer_type_label exists after backfill")

got = dict(c.execute("SELECT id, defer_type_label FROM proposals").fetchall())
check(got["p1"] == "defer-needs-decision", f"p1 -> {got['p1']}")
check(got["p2"] == "defer-theoretical", f"p2 -> {got['p2']}")
check(got["p3"] == "defer-needs-hardware", f"p3 -> {got['p3']}")
check(got["p4"] == "defer-dep-blocked", f"p4 -> {got['p4']}")
check(got["p5"] is None, "non-defer row p5 left untouched (status != 'defer')")
check(got["p6"] is None, "unmatched row p6 left NULL, not guessed")
c.close()

# idempotent re-run: second backfill relabels the same set, does not duplicate/error
result2 = bdl.backfill(DB)
check(result2["labeled"] == 4, "second backfill run is idempotent (same 4 labeled)")

print(f"\n{N[0] - len(FAILS)}/{N[0]} passed")
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print(" -", f)
    sys.exit(1)
