"""wq#1654 tests — decisions ingest into a fixture state_facts DB (schema
mirrors ops/wq1632-lumen-state-lifecycle/test_state_lifecycle.py)."""
import os
import shutil
import sqlite3

import pytest

import ingest_decisions as idm
import state_lifecycle as sl

HERE = os.path.dirname(os.path.abspath(__file__))
WQ1632 = os.path.join(HERE, "..", "wq1632-lumen-state-lifecycle")

SCHEMA = """
CREATE TABLE state_facts (
  id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  value TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  ingested_at TEXT,
  invalidated_at TEXT,
  source_ref TEXT,
  confidence REAL,
  episode_id TEXT,
  half_life_days REAL,
  last_confirmed_at TEXT,
  fact_class TEXT,
  render_tier TEXT
);
CREATE UNIQUE INDEX uq_open ON state_facts(subject, predicate)
  WHERE valid_to IS NULL;
"""


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.yaml"
    shutil.copy(os.path.join(WQ1632, "registry.seed.yaml"), path)
    return sl.Registry(str(path))


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def test_ingests_one_row_per_timer(conn, registry):
    results = idm.ingest_all(conn, registry)
    assert len(results) == len(idm.DECISIONS)
    rows = conn.execute(
        "SELECT subject, value FROM state_facts WHERE predicate=? AND valid_to IS NULL",
        (idm.PREDICATE,),
    ).fetchall()
    assert {r["subject"] for r in rows} == {u for u, *_ in idm.DECISIONS}
    assert all(r["value"] == "keep_fix_label" for r in rows)


def test_rerun_is_a_reconfirm_not_a_duplicate(conn, registry):
    idm.ingest_all(conn, registry)
    second = idm.ingest_all(conn, registry)
    assert all(r["action"] == "reconfirmed" for r in second)
    count = conn.execute(
        "SELECT COUNT(*) c FROM state_facts WHERE predicate=?", (idm.PREDICATE,)
    ).fetchone()["c"]
    assert count == len(idm.DECISIONS)


def test_registers_new_predicate_in_registry(conn, registry):
    assert registry.lookup(idm.PREDICATE) is None
    idm.ingest_all(conn, registry)
    assert registry.lookup(idm.PREDICATE) is not None
