"""aq_daily_ack_extend.py unit tests (wq#3176).

Zero live services: alerts.db is a tmp sqlite file with the same schema aq.py
creates/expects, same convention as ops/wq1649-aq-source-escalate's tests.
"""
import datetime
import importlib.util
import os
import sqlite3
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(TESTS_DIR)

spec = importlib.util.spec_from_file_location(
    "aq_daily_ack_extend", os.path.join(MODULE_DIR, "aq_daily_ack_extend.py"))
ade = importlib.util.module_from_spec(spec)
sys.modules["aq_daily_ack_extend"] = ade
spec.loader.exec_module(ade)


@pytest.fixture
def env(tmp_path, monkeypatch):
    aq_db = tmp_path / "alerts.db"
    conn = sqlite3.connect(str(aq_db))
    conn.executescript("""
        CREATE TABLE alerts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT, key TEXT, title TEXT, message TEXT,
          status TEXT, count INTEGER, priority TEXT,
          first_ts TEXT, last_ts TEXT, owner TEXT, note TEXT, resolved_ts TEXT);
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(ade, "AQ_DB", str(aq_db))
    monkeypatch.setattr(ade, "ACK_AFTER_DAYS", 7)
    monkeypatch.setattr(ade, "PER_SOURCE_CAP", 3)

    db_path_str = str(aq_db)

    class Env:
        db_path = db_path_str

        def insert(self, source, key, status="open", count=1, priority="default",
                    title="t", last_ts="2026-09-01T00:00:00Z"):
            c = sqlite3.connect(self.db_path)
            c.execute(
                "INSERT INTO alerts(source,key,title,message,status,count,priority,"
                "first_ts,last_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                (source, key, title, "msg", status, count, priority, last_ts, last_ts))
            c.commit()
            c.close()
            return c

        def rows(self, **where):
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            clause = " AND ".join(f"{k}=?" for k in where) or "1=1"
            rs = c.execute(f"SELECT * FROM alerts WHERE {clause}", tuple(where.values())).fetchall()
            c.close()
            return rs

    return Env()


def test_recovered_newer_than_failed_acks_both(env):
    env.insert("failed-unit", "Failed unit: langfuse-evaluator.service", last_ts="2026-09-10T00:00:00Z")
    env.insert("failed-unit", "Recovered: langfuse-evaluator.service", last_ts="2026-09-11T00:00:00Z")
    n = ade.ack_recovered_failed_pairs(ade.db())
    assert n == 2
    rows = env.rows(source="failed-unit")
    assert all(r["status"] == "acked" for r in rows)
    assert all("recovered newer than failed" in (r["note"] or "") for r in rows)


def test_failed_newer_than_recovered_leaves_both_open(env):
    """Unit failed again after its last recovery -- still down, don't ack."""
    env.insert("failed-unit", "Recovered: flapping.service", last_ts="2026-09-10T00:00:00Z")
    env.insert("failed-unit", "Failed unit: flapping.service", last_ts="2026-09-11T00:00:00Z")
    n = ade.ack_recovered_failed_pairs(ade.db())
    assert n == 0
    rows = env.rows(source="failed-unit")
    assert all(r["status"] == "open" for r in rows)


def test_recovered_with_no_failed_twin_left_alone(env):
    env.insert("failed-unit", "Recovered: orphan.service")
    n = ade.ack_recovered_failed_pairs(ade.db())
    assert n == 0


def test_stale_info_class_acks_recurring_rows(env):
    """Existing aq_autoack.py rule requires count=1; this rule must also reach
    a recurring (count>1) default-priority row once it's gone stale."""
    env.insert("failed-unit", "Failed unit: quiet-now.service", count=42,
               priority="default", last_ts="2026-08-01T00:00:00Z")
    n = ade.ack_stale_info_class(ade.db())
    assert n == 1
    rows = env.rows(source="failed-unit")
    assert rows[0]["status"] == "acked"


def test_stale_info_class_never_touches_high_priority(env):
    env.insert("jay-watch", "row1", priority="high", last_ts="2026-08-01T00:00:00Z")
    n = ade.ack_stale_info_class(ade.db())
    assert n == 0


def test_stale_info_class_leaves_recent_rows_open(env):
    recent = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    env.insert("failed-unit", "Failed unit: active.service", priority="default",
               last_ts=recent)
    n = ade.ack_stale_info_class(ade.db())
    assert n == 0


def test_stale_info_class_excludes_minerva_wrappers(env):
    env.insert("minerva", "row1", priority="default", title="[deploy_ready] x",
               last_ts="2026-08-01T00:00:00Z")
    n = ade.ack_stale_info_class(ade.db())
    assert n == 0


def test_cap_per_source_keeps_newest_and_acks_overflow(env):
    for i in range(5):
        env.insert("jay-watch", f"row{i}", priority="high",
                   last_ts=f"2026-09-0{i+1}T00:00:00Z")
    n = ade.cap_per_source(ade.db())  # PER_SOURCE_CAP=3 in fixture
    assert n == 2
    open_rows = env.rows(source="jay-watch", status="open")
    assert len(open_rows) == 3
    assert {r["key"] for r in open_rows} == {"row2", "row3", "row4"}


def test_cap_per_source_under_cap_untouched(env):
    for i in range(2):
        env.insert("jay-watch", f"row{i}", priority="high")
    n = ade.cap_per_source(ade.db())
    assert n == 0


def test_dry_run_counts_but_writes_nothing(env, capsys, monkeypatch):
    env.insert("failed-unit", "Failed unit: quiet-now.service", last_ts="2026-08-01T00:00:00Z")
    for i in range(5):
        env.insert("jay-watch", f"row{i}", priority="high")
    monkeypatch.setattr(ade, "DRY_RUN", False)
    assert ade.run(dry_run=True) == 0
    assert "1 row(s) would ack (stale" in capsys.readouterr().out
    assert all(r["status"] == "open" for r in env.rows())
    ade.run()
    assert len(env.rows(status="acked")) == 3
