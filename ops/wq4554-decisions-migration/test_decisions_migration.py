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


class _FakeCur:
    """Minimal psycopg2-cursor stand-in: knows which table names exist."""

    def __init__(self, existing):
        self.existing = existing
        self._last = None

    def execute(self, sql, params=None):
        self._last = params[0]

    def fetchone(self):
        return (self._last if self._last in self.existing else None,)


def test_source_table_falls_back_to_frozen_copy():
    """wq#4578: install.sh's step 4 renames the source tables to retired_*, so
    a second run of the installer must read the frozen copy instead of dying
    on UndefinedTable — re-install is the only deploy path for the renderer."""
    assert md._source_table(_FakeCur({"fleet_decisions"}), "fleet_decisions") \
        == "fleet_decisions"
    assert md._source_table(_FakeCur({"retired_fleet_decisions"}),
                            "fleet_decisions") == "retired_fleet_decisions"
    assert md._source_table(_FakeCur(set()), "fleet_facts") is None


def test_parse_state_lines_binds_last_since_clause():
    """wq#4578 regression, carried into phase 2 because this bundle's
    context_builder.py is the copy that is actually installed: the value group
    is greedy, so a value whose own free text contains a "(since DATE)"
    fragment binds to the LAST clause — the one lumen_state.py appends. The
    non-greedy `.*?` this replaces parsed the first line below as
    value="flaky", since="2020-01-01": truncated, six years stale, no error.
    Fixture is the pinned one from the phase-1 bundle (sibling dir, committed,
    immutable) so both copies of the parser are probed against identical bytes.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "wq4553-context-builder-live-roster", "fixtures",
        "state_embedded_since_wq4578.txt")
    with open(path, encoding="utf-8") as fh:
        facts = cb.parse_state_lines(fh.read())
    got = {(f["subject"], f["predicate"]): (f["value"], f["since"]) for f in facts}
    assert len(facts) == 6
    assert got[("testhost", "status")] == (
        "flaky (since 2020-01-01) but recovered", "2026-10-11")
    assert got[("dualhost", "lan_ip")] == (
        "192.168.8.44 (static lease since 2025-12-01; see wq#4539)", "2026-08-14")
    assert got[("tailhost", "role")] == ("watchdog (since 2024-05-05)", "2026-10-22")
    assert got[("emptyhost", "status")] == ("", "2026-10-20")
