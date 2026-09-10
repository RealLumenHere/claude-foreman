#!/usr/bin/env python3
"""Tests for wq#1610 note_contradiction_scan.py against a scratch minerva-shaped
sqlite DB. Nothing here touches the live minerva.db.

    python3 ops/wq1610-minerva-archival-contradictions/test_note_contradiction_scan.py

Exits 0 when every case passes.
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import note_contradiction_scan as ncs  # noqa: E402

SCHEMA = """
CREATE TABLE state_facts (
    id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, value TEXT,
    valid_from TEXT, valid_to TEXT
);
CREATE TABLE archival_notes (
    id TEXT PRIMARY KEY, kind TEXT, title TEXT, content TEXT,
    created_at TEXT, revoked_at TEXT, superseded_by TEXT
);
CREATE TABLE note_merges (
    absorbed_id TEXT, keep_id TEXT, sim REAL, method TEXT, ts TEXT
);
"""

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def seed(path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO state_facts (id,subject,predicate,value,valid_from,valid_to) "
        "VALUES (?,?,?,?,?,?)",
        [
            # closed (superseded) fact — structured value, short enum
            ("f-old-role", "nephthys", "role", "ACTIVE",
             "2026-01-01T00:00:00Z", "2026-08-01T00:00:00Z"),
            ("f-new-role", "nephthys", "role", "DECOMMISSIONED",
             "2026-08-01T00:00:00Z", None),
            # closed IP fact
            ("f-old-ip", "pbox", "lan_ip", "10.0.0.5",
             "2026-01-01T00:00:00Z", "2026-08-15T00:00:00Z"),
            ("f-new-ip", "pbox", "lan_ip", "10.0.0.9",
             "2026-08-15T00:00:00Z", None),
            # closed status fact whose VALUE is itself a NEGATION_RE word
            ("f-old-status", "pbox", "status", "RETIRED",
             "2026-01-01T00:00:00Z", "2026-08-10T00:00:00Z"),
            ("f-new-status", "pbox", "status", "LIVE",
             "2026-08-10T00:00:00Z", None),
            # narrative-shaped closed value — must NOT be indexed (>60 chars)
            ("f-old-narr", "seshat", "notes",
             "long free-form prose describing a state that is definitely over sixty characters",
             "2026-01-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        ],
    )
    con.executemany(
        "INSERT INTO archival_notes (id,kind,title,content,created_at,revoked_at,"
        "superseded_by) VALUES (?,?,?,?,?,?,?)",
        [
            # plain stale hit: asserts the dead role with no negation/changelog/as-of cover
            ("n-stale", "session", "nephthys status",
             "nephthys.role is ACTIVE and handles the archive job.",
             "2026-08-20T00:00:00Z", None, None),
            # changelog note: dead + current both present -> documents the transition
            ("n-changelog", "session", "nephthys migration",
             "nephthys.role was ACTIVE, now DECOMMISSIONED after the 08-01 cutover.",
             "2026-08-20T00:00:00Z", None, None),
            # negation line: 'former' marks it as dead prose, not an assertion
            ("n-negation", "session", "nephthys history",
             "nephthys.role was formerly ACTIVE before decommission.",
             "2026-08-20T00:00:00Z", None, None),
            # as-of note: predates valid_to -> correct historical record
            ("n-asof", "session", "nephthys early note",
             "nephthys.role is ACTIVE right now.",
             "2026-03-01T00:00:00Z", None, None),
            # IP hit
            ("n-ip-stale", "session", "pbox network",
             "pbox lan_ip 10.0.0.5 is reachable.",
             "2026-08-20T00:00:00Z", None, None),
            # dead VALUE itself is a NEGATION_RE word ("RETIRED") — must still flag
            ("n-value-is-negation-word", "session", "pbox status",
             "pbox status is RETIRED and needs a health check.",
             "2026-08-20T00:00:00Z", None, None),
            # narrative dead value should never match (excluded from index entirely)
            ("n-narrative", "session", "seshat note",
             "long free-form prose describing a state that is definitely over sixty characters",
             "2026-08-20T00:00:00Z", None, None),
            # already-revoked note: excluded from scan outright
            ("n-revoked", "session", "revoked nephthys note",
             "nephthys.role is ACTIVE.",
             "2026-08-20T00:00:00Z", "2026-08-21T00:00:00Z", None),
            # absorbed note: excluded via note_merges
            ("n-absorbed", "session", "absorbed nephthys note",
             "nephthys.role is ACTIVE.",
             "2026-08-20T00:00:00Z", None, None),
        ],
    )
    con.execute("INSERT INTO note_merges (absorbed_id,keep_id,sim,method,ts) "
                "VALUES ('n-absorbed','n-stale',0.99,'exact-auto','2026-08-22T00:00:00Z')")
    con.commit()
    con.close()


def rows(db, query, params=()):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(query, params).fetchall()]
    finally:
        con.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "minerva.db")
        seed(db)

        conn = sqlite3.connect(db, timeout=30)
        summary1 = ncs.scan(conn, dry_run=False)
        conn.close()

        # 7 live (unrevoked, unabsorbed) notes: n-stale, n-changelog, n-negation,
        # n-asof, n-ip-stale, n-narrative, n-value-is-negation-word — n-revoked and
        # n-absorbed excluded.
        check("notes_scanned excludes revoked+absorbed", summary1["notes_scanned"] == 7,
              summary1)

        contras = rows(db, "SELECT * FROM note_contradictions ORDER BY note_id")
        by_note = {r["note_id"] for r in contras}
        check("plain stale note flagged", "n-stale" in by_note)
        check("ip stale note flagged", "n-ip-stale" in by_note)
        check("dead value that is itself a NEGATION_RE word still flagged",
              "n-value-is-negation-word" in by_note)
        check("changelog note NOT flagged (transition documented)",
              "n-changelog" not in by_note)
        check("negation note NOT flagged (dead value marked as such)",
              "n-negation" not in by_note)
        check("as-of note NOT flagged (predates valid_to)", "n-asof" not in by_note)
        check("narrative value NOT flagged (not structured-token shaped)",
              "n-narrative" not in by_note)
        check("revoked note never scanned", "n-revoked" not in by_note)
        check("absorbed note never scanned", "n-absorbed" not in by_note)

        stale_row = next((r for r in contras if r["note_id"] == "n-stale"), None)
        check("stale row carries dead_value=ACTIVE", stale_row and stale_row["dead_value"] == "ACTIVE",
              stale_row)
        check("stale row carries current_value=DECOMMISSIONED",
              stale_row and stale_row["current_value"] == "DECOMMISSIONED", stale_row)
        check("stale row is open (cleared_at NULL)",
              stale_row and stale_row["cleared_at"] is None, stale_row)

        n_after_first = len(rows(db, "SELECT * FROM note_contradictions"))

        # Idempotency: re-running with no data change must not duplicate rows.
        conn = sqlite3.connect(db, timeout=30)
        summary2 = ncs.scan(conn, dry_run=False)
        conn.close()
        n_after_second = len(rows(db, "SELECT * FROM note_contradictions"))
        check("second run writes no new rows", summary2["written"] == 0,
              summary2)
        check("row count stable across runs", n_after_first == n_after_second,
              f"{n_after_first} -> {n_after_second}")

        # Clearing: fix the note text so it no longer asserts the dead value, then
        # the previously-open row for it must get cleared_at set.
        conn = sqlite3.connect(db, timeout=30)
        conn.execute(
            "UPDATE archival_notes SET content=? WHERE id='n-stale'",
            ("nephthys.role is DECOMMISSIONED now.",))
        conn.commit()
        summary3 = ncs.scan(conn, dry_run=False)
        conn.close()
        check("clearing pass clears the fixed note's row", summary3["cleared"] >= 1,
              summary3)
        cleared_row = rows(db, "SELECT * FROM note_contradictions WHERE note_id='n-stale'")[0]
        check("n-stale row now has cleared_at set", cleared_row["cleared_at"] is not None,
              cleared_row)

        # Dry-run must not touch the DB at all.
        n_before_dry = len(rows(db, "SELECT * FROM note_contradictions"))
        conn = sqlite3.connect(db, timeout=30)
        conn.execute("UPDATE archival_notes SET content=? WHERE id='n-changelog'",
                     ("nephthys.role is ACTIVE.",))
        conn.commit()
        ncs.scan(conn, dry_run=True)
        conn.close()
        n_after_dry = len(rows(db, "SELECT * FROM note_contradictions"))
        check("dry-run writes nothing", n_before_dry == n_after_dry,
              f"{n_before_dry} -> {n_after_dry}")

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("\nall checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
