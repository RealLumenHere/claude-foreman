"""wq#2217 verifier probe against wq#2212 (PR 773).

`_park` fingerprints g.t (`wq show --json`) while `partition` re-fingerprints the sqlite row on the
next pass. A column whose JSON value differs from the sqlite value would release every park row as
`fingerprint_changed` and silently stop parking, so the snapshot and the row must agree.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gate_store import (  # noqa: E402,F401
    denv, add_ticket, db_exec, gate_store, fd)


def _row(denv, tid):
    con = sqlite3.connect(denv["wq_db"])
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
    finally:
        con.close()


def test_snapshot_fingerprint_matches_row_fingerprint_for_every_column(denv):
    dep = add_ticket("the dep")
    tid = add_ticket("multi-line\ntitle", deps=str(dep))
    db_exec(denv, "UPDATE tasks SET detail=? WHERE id=?", ("line1\nline2 é \"q\"", tid))
    row = _row(denv, tid)
    snap = fd.router.wq_show(tid)
    cols = sorted(row)
    diffs = {c: (row[c], snap.get(c)) for c in cols if row[c] != snap.get(c)}
    assert gate_store.fingerprint(tid, cols, "salt", row=snap) \
        == gate_store.fingerprint(tid, cols, "salt"), diffs


def test_snapshot_fingerprint_matches_row_when_deps_is_null(denv):
    tid = add_ticket("no deps")
    inputs = ("deps", "model", "effort", "archetype")
    assert gate_store.fingerprint(tid, inputs, "s", row=fd.router.wq_show(tid)) \
        == gate_store.fingerprint(tid, inputs, "s")
