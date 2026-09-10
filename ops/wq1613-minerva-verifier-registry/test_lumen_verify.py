"""wq#1613 tests — verifier registry + lumen_verify sweep.

Fixture DB mirrors the state_facts schema from wq#1632's test (§1.1 of that
ticket's DESIGN.md); lumen_verify.migrate() adds the wq#1613 columns on top,
which must be additive and idempotent (MINERVA_STALENESS_REVIEW.md §3.1).
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import lumen_verify as lv

HERE = os.path.dirname(os.path.abspath(__file__))

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

NOW = datetime(2026, 9, 10, 6, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.yaml"
    import shutil
    shutil.copy(os.path.join(HERE, "registry.yaml"), path)
    return lv.Registry(str(path))


def seed(conn, subject, predicate, value, age_days, fact_class="identity",
         source_kind=None, verify_interval_days=None):
    ts = iso(NOW - timedelta(days=age_days))
    cur = conn.execute(
        "INSERT INTO state_facts (subject, predicate, value, valid_from, "
        "ingested_at, last_confirmed_at, fact_class, source_ref) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (subject, predicate, value, ts, ts, ts, fact_class, "seed"),
    )
    conn.commit()
    fid = cur.lastrowid
    lv.migrate(conn)
    if source_kind:
        conn.execute("UPDATE state_facts SET source_kind=? WHERE id=?", (source_kind, fid))
    if verify_interval_days is not None:
        conn.execute("UPDATE state_facts SET verify_interval_days=? WHERE id=?",
                     (verify_interval_days, fid))
    conn.commit()
    return fid


# --------------------------------------------------------------- migration

def test_migrate_adds_columns_idempotently(conn):
    lv.migrate(conn)
    lv.migrate(conn)  # must not raise on the second pass
    cols = {r[1] for r in conn.execute("PRAGMA table_info(state_facts)")}
    assert {"source_kind", "verify_recipe", "verify_interval_days",
            "contradicted_at", "derived_from"} <= cols
    conn.execute("SELECT * FROM fact_verifications")  # table exists


# ------------------------------------------------------------------ due_facts

def test_due_by_class_default(conn, registry):
    # identity default is 90d; 91 days old is due, 10 days old is not.
    seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=91, fact_class="identity")
    seed(conn, "hathor", "role", "worker", age_days=10, fact_class="identity")
    batches = lv.due_facts(conn, registry, NOW)
    due_preds = {f["predicate"] for facts in batches.values() for f in facts}
    assert "tailscale_ip" in due_preds
    assert "role" not in due_preds  # 'role' has no probe assigned at all


def test_due_skips_predicate_with_no_probe(conn, registry):
    seed(conn, "hathor", "hardware", "mac-mini-m2", age_days=999, fact_class="hardware")
    batches = lv.due_facts(conn, registry, NOW)
    assert not batches


def test_fact_level_interval_overrides_registry(conn, registry):
    # tailscale_ip normally 1d; a fact-level override of 30d should suppress it at 5d old.
    seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5, fact_class="identity",
         verify_interval_days=30)
    batches = lv.due_facts(conn, registry, NOW)
    assert not batches


def test_batches_grouped_per_probe_and_subject(conn, registry):
    seed(conn, "hathor", "lan_ip", "192.168.1.5", age_days=2, fact_class="identity")
    seed(conn, "hathor", "ssh_user", "lumen", age_days=2, fact_class="identity")
    seed(conn, "thoth", "lan_ip", "192.168.1.6", age_days=2, fact_class="identity")
    batches = lv.due_facts(conn, registry, NOW)
    assert set(batches) == {("fleet_inventory", "hathor"), ("fleet_inventory", "thoth")}
    assert len(batches[("fleet_inventory", "hathor")]) == 2


# ------------------------------------------------------------------ reconcile

def get_fact(conn, fid):
    return dict(conn.execute("SELECT * FROM state_facts WHERE id=?", (fid,)).fetchone())


def test_reconcile_confirms_on_match(conn, registry):
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    fact = get_fact(conn, fid)
    outcome = lv.reconcile(conn, fact, "tailscale_ip", "100.1.1.1", None, NOW)
    assert outcome == "confirmed"
    row = get_fact(conn, fid)
    assert row["last_confirmed_at"] == iso(NOW)
    assert row["valid_to"] is None
    verif = conn.execute("SELECT outcome FROM fact_verifications WHERE fact_id=?",
                          (fid,)).fetchone()
    assert verif["outcome"] == "confirmed"


def test_reconcile_supersedes_on_mismatch(conn, registry):
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5,
               source_kind="observed")
    fact = get_fact(conn, fid)
    outcome = lv.reconcile(conn, fact, "tailscale_ip", "100.1.1.2", None, NOW)
    assert outcome == "superseded"
    old = get_fact(conn, fid)
    assert old["valid_to"] == iso(NOW)
    new = conn.execute(
        "SELECT * FROM state_facts WHERE subject='hathor' AND predicate='tailscale_ip' "
        "AND valid_to IS NULL"
    ).fetchone()
    assert dict(new)["value"] == "100.1.1.2"
    assert dict(new)["source_kind"] == "observed"


def test_reconcile_error_never_mutates(conn, registry):
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    fact = get_fact(conn, fid)
    outcome = lv.reconcile(conn, fact, "tailscale_ip", None, "tailscale rc=1", NOW)
    assert outcome == "unknown"
    row = get_fact(conn, fid)
    assert row["valid_to"] is None
    assert row["value"] == "100.1.1.1"
    assert row["last_confirmed_at"] != iso(NOW)


def test_reconcile_frank_stated_never_superseded(conn, registry, monkeypatch):
    filed = []
    monkeypatch.setattr(lv, "file_conflict",
                         lambda *a, **k: filed.append(a))
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5,
               source_kind="frank_stated")
    fact = get_fact(conn, fid)
    outcome = lv.reconcile(conn, fact, "tailscale_ip", "100.1.1.99", None, NOW)
    assert outcome == "unknown"
    row = get_fact(conn, fid)
    assert row["valid_to"] is None
    assert row["value"] == "100.1.1.1"  # never overwritten
    assert filed and filed[0][0] == "hathor"
    verif = conn.execute("SELECT outcome, observed FROM fact_verifications WHERE fact_id=?",
                          (fid,)).fetchone()
    assert verif["outcome"] == "unknown"
    assert verif["observed"] == "100.1.1.99"  # conflict is on record, just not applied


# ---------------------------------------------------------------------- sweep

def test_sweep_probe_exception_is_fail_open(conn, registry, monkeypatch):
    def boom(subject, facts):
        raise RuntimeError("ssh timed out")
    monkeypatch.setitem(lv.PROBES, "tailscale_ip", boom)
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    result = lv.sweep(conn, registry, now=NOW)
    assert result["counts"]["unknown"] == 1
    assert get_fact(conn, fid)["valid_to"] is None


def test_sweep_dry_run_does_not_write(conn, registry, monkeypatch):
    monkeypatch.setitem(lv.PROBES, "tailscale_ip",
                         lambda subject, facts: {(subject, f["predicate"]): (f["value"], None)
                                                  for f in facts})
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    result = lv.sweep(conn, registry, now=NOW, dry_run=True)
    assert result["counts"]["confirmed"] == 1
    assert conn.execute("SELECT count(*) FROM fact_verifications").fetchone()[0] == 0
    assert get_fact(conn, fid)["last_confirmed_at"] != iso(NOW)


# --------------------------------------------------------------- node_liveness

def test_probe_node_liveness_unreachable_is_offline_not_error(monkeypatch):
    # wq#1620: ssh AND tailscale ping both failing (khepri/wadjet's actual
    # state) must be reported as the observation "offline", not an unknown
    # probe error -- otherwise a genuinely offline node's stale POWERED ON /
    # available fact can never be superseded by the sweep.
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (255, "", "unreachable"))
    facts = [{"predicate": "availability", "value": "available"}]
    result = lv.probe_node_liveness("wadjet", facts)
    assert result[("wadjet", "availability")] == ("offline", None)


def test_probe_node_liveness_ssh_ok_is_available(monkeypatch):
    def fake_run(argv, timeout=20):
        return (0, "", "") if argv[0] == "ssh" else (1, "", "no route")
    monkeypatch.setattr(lv, "run", fake_run)
    facts = [{"predicate": "availability", "value": "offline"}]
    result = lv.probe_node_liveness("khepri", facts)
    assert result[("khepri", "availability")] == ("available", None)


def test_sweep_supersedes_stale_availability_when_node_goes_offline(conn, registry, monkeypatch):
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (255, "", "unreachable"))
    fid = seed(conn, "wadjet", "availability", "available", age_days=5)
    result = lv.sweep(conn, registry, now=NOW)
    assert result["counts"]["superseded"] == 1
    assert get_fact(conn, fid)["valid_to"] == iso(NOW)
