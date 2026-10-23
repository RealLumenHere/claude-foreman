#!/usr/bin/env python3
"""migrate_decisions.py — wq#4554 (wq#4539 phase 2): copy PG fleet_decisions
rows into minerva.db's own fleet_decisions table (schema.sql in this dir),
audited the same way minerva.db audits every other write — an append to
`memory_audit` (minerva's write-trust ledger; see minerva.db `memory_audit`
schema: seq/ts/action/table_name/row_id/lane/agent_id/trust/source_ref/
before_sha/after_sha/detail/invariant) per inserted row, not a silent sqlite
INSERT. No separate memoryd RPC client exists in this lane's accessible code
(minerva/memory/fm_db.py's log_event() targets PG memory_events, the store
being retired by this same ticket) — this writes straight to minerva's own
audit table, the mechanism memoryd's own writers use, rather than inventing a
second unaudited path.

Idempotent: INSERT OR IGNORE on id, so a re-run after a partial failure never
double-inserts or double-audits (checked against memory_audit first).

Usage: migrate_decisions.py --minerva-db PATH [--dry-run]
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

sys.path.insert(0, "/home/lumen/foreman/harness")
import recipe_store as rs  # noqa: E402

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
LANE = "wq4554"
SOURCE_REF = "wq#4554"


def _source_table(cur, name):
    """wq#4578: this installer's own step 4 renames the source tables to
    `retired_*`, so a second run of install.sh died here with UndefinedTable —
    i.e. the bundle could never be re-installed, and re-install is the only
    deploy path for a renderer that lives untracked in minerva/memory. Read the
    frozen copy when the live name is gone (the mirror is INSERT OR IGNORE, so
    a re-run is a no-op resync), and return None when neither exists."""
    for candidate in (name, "retired_" + name):
        cur.execute("SELECT to_regclass(%s)", (candidate,))
        if cur.fetchone()[0] is not None:
            return candidate
    return None


def fetch_pg_rows():
    conn = rs._connect()
    try:
        cur = conn.cursor()
        table = _source_table(cur, "fleet_decisions")
        if table is None:
            return []
        cur.execute(
            "SELECT id, decision, rationale, scope, status, created_at, supersedes "
            "FROM " + table + " ORDER BY id")
        rows = cur.fetchall()
    finally:
        conn.close()
    return rows


def fetch_pg_facts():
    """All fleet_facts rows, not just the claude_memory.md%-sourced ones the
    render currently uses — freezing the table is permanent, so the mirror
    keeps everything PG had, not just today's render filter."""
    conn = rs._connect()
    try:
        cur = conn.cursor()
        table = _source_table(cur, "fleet_facts")
        if table is None:
            return []
        cur.execute(
            "SELECT id, subject, attribute, value, source, status, expires_at "
            "FROM " + table + " ORDER BY id")
        rows = cur.fetchall()
    finally:
        conn.close()
    return rows


def with_superseded_by(rows):
    """Derive superseded_by/superseded_ts for each row from the reverse of
    `supersedes` (PG records "this row supersedes X", not the other way)."""
    supersedes_of = {r[0]: r[6] for r in rows}  # id -> supersedes
    created_at_of = {r[0]: r[5] for r in rows}
    superseded_by = {}
    for rid, old_id in supersedes_of.items():
        if old_id is not None:
            superseded_by[old_id] = rid
    out = []
    for (rid, decision, rationale, scope, status, created_at, supersedes) in rows:
        sb = superseded_by.get(rid)
        sts = created_at_of.get(sb) if sb is not None else None
        out.append((rid, decision, rationale, scope, status,
                    str(created_at), supersedes, sb,
                    str(sts) if sts is not None else None))
    return out


def migrate(minerva_db_path, dry_run=False):
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = fh.read()

    decision_rows = with_superseded_by(fetch_pg_rows())
    fact_rows = fetch_pg_facts()
    report = {"pg_decisions": len(decision_rows), "pg_facts": len(fact_rows),
              "decisions_inserted": 0, "decisions_skipped": 0,
              "facts_inserted": 0, "facts_skipped": 0}

    if dry_run:
        report["dry_run"] = True
        return report

    con = sqlite3.connect(minerva_db_path, timeout=10)
    try:
        con.executescript(schema)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for row in decision_rows:
            rid = row[0]
            if _exists(con, "fleet_decisions", rid):
                report["decisions_skipped"] += 1
                continue
            con.execute(
                "INSERT OR IGNORE INTO fleet_decisions "
                "(id, decision, rationale, scope, status, created_at, "
                " supersedes, superseded_by, superseded_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)", row)
            _audit(con, now, "fleet_decisions", rid)
            report["decisions_inserted"] += 1
        for row in fact_rows:
            rid = row[0]
            if _exists(con, "fleet_facts", rid):
                report["facts_skipped"] += 1
                continue
            con.execute(
                "INSERT OR IGNORE INTO fleet_facts "
                "(id, subject, attribute, value, source, status, expires_at) "
                "VALUES (?,?,?,?,?,?,?)",
                tuple(str(v) if v is not None else None for v in row))
            _audit(con, now, "fleet_facts", rid)
            report["facts_inserted"] += 1
        con.commit()
    finally:
        con.close()
    return report


def _exists(con, table, rid):
    cur = con.execute("SELECT 1 FROM %s WHERE id = ?" % table, (rid,))
    return cur.fetchone() is not None


def _audit(con, ts, table_name, rid):
    con.execute(
        "INSERT INTO memory_audit "
        "(ts, action, table_name, row_id, lane, agent_id, trust, "
        " source_ref, detail) VALUES (?,?,?,?,?,?,?,?,?)",
        (ts, "migrate_insert", table_name, str(rid), LANE, LANE, "audited",
         SOURCE_REF, "wq#4554 phase 2: copied from PG %s id=%d" % (table_name, rid)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minerva-db", default="/home/lumen/minerva/minerva.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    report = migrate(args.minerva_db, dry_run=args.dry_run)
    print(report)


if __name__ == "__main__":
    main()
