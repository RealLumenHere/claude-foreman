"""wq#2192 tests — repair_clobbered_facts undoes verifier clobbers, once."""
import sqlite3

import pytest

import repair_clobbered_facts as rc

SCHEMA = """
CREATE TABLE state_facts (
  id INTEGER PRIMARY KEY, subject TEXT NOT NULL, predicate TEXT NOT NULL,
  value TEXT NOT NULL, valid_from TEXT, valid_to TEXT, invalidated_at TEXT,
  source_ref TEXT
);
CREATE UNIQUE INDEX uq_open ON state_facts(subject, predicate) WHERE valid_to IS NULL;
"""

T = "2026-09-12T06:00:14+00:00"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def add(conn, subject, predicate, value, valid_from, valid_to, source_ref):
    conn.execute(
        "INSERT INTO state_facts (subject, predicate, value, valid_from, valid_to, "
        "source_ref) VALUES (?,?,?,?,?,?)",
        (subject, predicate, value, valid_from, valid_to, source_ref))


def open_value(conn, subject, predicate):
    row = conn.execute(
        "SELECT value FROM state_facts WHERE subject=? AND predicate=? AND valid_to IS NULL",
        (subject, predicate)).fetchone()
    return row["value"] if row else None


def test_repair_restores_superseded_values(conn):
    add(conn, "bes", "status", "RESTORED", "2026-07-18", T, "verified")
    add(conn, "bes", "status", "inactive", T, None, "verify:service_state")
    add(conn, "apep", "netdata_version", "2.11.0", "2026-08-23", T, "wq#797")
    add(conn, "apep", "netdata_version", "Linux", T, None, "verify:os_kernel")
    conn.commit()
    assert len(rc.repair(conn, apply=True)) == 2
    assert open_value(conn, "bes", "status") == "RESTORED"
    assert open_value(conn, "apep", "netdata_version") == "2.11.0"


def test_repair_is_idempotent(conn):
    add(conn, "bes", "status", "RESTORED", "2026-07-18", T, "verified")
    add(conn, "bes", "status", "inactive", T, None, "verify:service_state")
    conn.commit()
    rc.repair(conn, apply=True)
    assert rc.repair(conn, apply=True) == []
    assert open_value(conn, "bes", "status") == "RESTORED"


def test_repair_without_predecessor_only_closes_clobber(conn):
    add(conn, "lumen", "dc_version", "Linux", T, None, "verify:os_kernel")
    conn.commit()
    rc.repair(conn, apply=True)
    assert open_value(conn, "lumen", "dc_version") is None


def test_dry_run_writes_nothing(conn):
    add(conn, "bes", "status", "RESTORED", "2026-07-18", T, "verified")
    add(conn, "bes", "status", "inactive", T, None, "verify:service_state")
    conn.commit()
    plan = rc.repair(conn)
    assert plan == [{"subject": "bes", "predicate": "status",
                     "clobber": "inactive", "restore": "RESTORED"}]
    assert open_value(conn, "bes", "status") == "inactive"


def test_repair_restores_endpoint_and_node_storage_clobbers(conn):
    # wq#2204: '200' over url/port, first-DIMM size over ram, lsblk byte sum over storage.
    add(conn, "ollama", "port", "11434", "2026-08-01", T, "wq#797")
    add(conn, "ollama", "port", "200", T, None, "verify:endpoint_live")
    add(conn, "ollama", "url", "http://ollama:11434/", "2026-08-01", T, "wq#797")
    add(conn, "ollama", "url", "200", T, None, "verify:endpoint_live")
    add(conn, "isis", "ram", "64GB DDR5", "2026-08-01", T, "manual")
    add(conn, "isis", "ram", "16GB", T, None, "verify:node_storage")
    add(conn, "isis", "storage", "2x 1TB NVMe", "2026-08-01", T, "manual")
    add(conn, "isis", "storage", "2000398934016", T, None, "verify:node_storage")
    conn.commit()
    assert len(rc.repair(conn, apply=True)) == 4
    assert open_value(conn, "ollama", "port") == "11434"
    assert open_value(conn, "ollama", "url") == "http://ollama:11434/"
    assert open_value(conn, "isis", "ram") == "64GB DDR5"
    assert open_value(conn, "isis", "storage") == "2x 1TB NVMe"
    assert rc.repair(conn, apply=True) == []


def test_repair_spares_legitimate_verifier_rows(conn):
    # kernel from os_kernel and a non-systemctl service_state value are real observations.
    add(conn, "lumen", "kernel", "Linux", T, None, "verify:os_kernel")
    add(conn, "thor", "availability", "available", T, None, "verify:node_liveness")
    add(conn, "isis", "status", "LIVE", T, None, "verify:service_state")
    conn.commit()
    assert rc.repair(conn, apply=True) == []
