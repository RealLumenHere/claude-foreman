"""wq#4554 probe: context_builder.py's decisions section renders from
minerva.db, and the superseded_by derivation in migrate_decisions.py is
correct — a pinned, synthetic fixture, not a live DB (immutable, re-runnable).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context_builder as cb  # noqa: E402
import migrate_decisions as md  # noqa: E402


def _fixture_db():
    con = sqlite3.connect(":memory:")
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as fh:
        con.executescript(fh.read())
    con.executescript("""
    CREATE TABLE memory_audit (seq INTEGER PRIMARY KEY, ts TEXT, action TEXT,
        table_name TEXT, row_id TEXT, lane TEXT, agent_id TEXT, trust TEXT,
        source_ref TEXT, before_sha TEXT, after_sha TEXT, detail TEXT,
        invariant TEXT);
    CREATE TABLE archival_notes (id INTEGER PRIMARY KEY, kind TEXT, title TEXT,
        source_ref TEXT, created_at TEXT, revoked_at TEXT, superseded_by TEXT);
    CREATE TABLE note_merges (absorbed_id INTEGER);
    """)
    con.execute("INSERT INTO fleet_decisions VALUES "
                "(1,'old rule','r1','permanent','superseded','2026-01-01',NULL,86,'2026-10-23')")
    con.execute("INSERT INTO fleet_decisions VALUES "
                "(86,'new rule','r2','permanent','active','2026-10-23',1,NULL,NULL)")
    con.commit()
    return con


def test_superseded_by_derivation():
    rows = [
        (1, "old", "r1", "permanent", "superseded", "2026-01-01", None),
        (86, "new", "r2", "permanent", "active", "2026-10-23", 1),
    ]
    out = {r[0]: r for r in md.with_superseded_by(rows)}
    assert out[1][7] == 86          # superseded_by
    assert out[1][8] == "2026-10-23"  # superseded_ts
    assert out[86][7] is None


def test_render_reads_minerva_not_pg():
    con = _fixture_db()
    text = cb.build(con)
    assert "new rule" in text
    # the superseded row must not render as current (phase 0 contract)
    assert "old rule" not in text
    con.close()
