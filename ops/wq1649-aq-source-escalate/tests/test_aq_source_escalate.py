"""aq_source_escalate.py unit tests (wq#1649).

Zero live services: alerts.db is a tmp sqlite file with the same schema
bin/aq_escalate.py creates/expects, and `wq add` is a fake shell script (same
convention as dispatcher/tests/test_dispatch_recover.py's henv fixture) so no ticket
ever touches the real work queue.
"""
import importlib.util
import os
import sqlite3
import stat
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(TESTS_DIR)

spec = importlib.util.spec_from_file_location(
    "aq_source_escalate", os.path.join(MODULE_DIR, "aq_source_escalate.py"))
ase = importlib.util.module_from_spec(spec)
sys.modules["aq_source_escalate"] = ase
spec.loader.exec_module(ase)


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


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

    ticket_counter = tmp_path / "ticket_counter"
    ticket_counter.write_text("100\n")
    wq_log = tmp_path / "wq_calls.jsonl"
    fake_wq = _script(tmp_path / "fake-wq", f"""#!/usr/bin/env bash
n=$(cat {ticket_counter})
n=$((n+1))
echo $n > {ticket_counter}
echo "$@" >> {wq_log}
echo "added task $n"
""")

    monkeypatch.setattr(ase, "AQ_DB", str(aq_db))
    monkeypatch.setattr(ase, "WQ_BIN", fake_wq)
    monkeypatch.setattr(ase, "FAILED_UNIT_THRESHOLD", 3)
    monkeypatch.setattr(ase, "SPAM_THRESHOLD", 5)
    monkeypatch.setattr(ase, "FINDING_EXIT_LIST", str(tmp_path / "no-such-file"))

    db_path_str = str(aq_db)
    wq_log_str = str(wq_log)

    class Env:
        db_path = db_path_str
        wq_log = wq_log_str

        def insert(self, source, key, status="open", count=1, priority="high",
                    title="t", first_ts="2026-09-01T00:00:00Z", last_ts="2026-09-10T00:00:00Z"):
            c = sqlite3.connect(self.db_path)
            c.execute(
                "INSERT INTO alerts(source,key,title,message,status,count,priority,"
                "first_ts,last_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                (source, key, title, "msg", status, count, priority, first_ts, last_ts))
            c.commit()
            c.close()

        def open_rows(self, **where):
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            clause = " AND ".join(f"{k}=?" for k in where) or "1=1"
            rows = c.execute(f"SELECT * FROM alerts WHERE {clause}", tuple(where.values())).fetchall()
            c.close()
            return rows

    return Env()


def test_failed_unit_group_creates_one_ticket_per_unit(env):
    for _ in range(4):
        env.insert("failed-unit", "flaky.service")
    n = ase.escalate_failed_units(ase.db())
    assert n == 1
    rows = env.open_rows(source="failed-unit", key="flaky.service")
    assert all(r["owner"] == "aq-escalation" for r in rows)
    assert "wq#101" in rows[0]["note"]


def test_failed_unit_below_threshold_no_ticket(env):
    for _ in range(2):
        env.insert("failed-unit", "quiet.service")
    n = ase.escalate_failed_units(ase.db())
    assert n == 0


def test_failed_unit_escalation_is_idempotent(env):
    for _ in range(3):
        env.insert("failed-unit", "flaky.service")
    c = ase.db()
    assert ase.escalate_failed_units(c) == 1
    env.insert("failed-unit", "flaky.service")  # a 4th row lands after escalation
    assert ase.escalate_failed_units(c) == 0  # already-escalated unit: no second ticket


def test_spam_source_suppresses_and_tickets_once(env):
    for i in range(6):
        env.insert("jay-watch", f"row{i}")
    n = ase.escalate_spam_sources(ase.db())
    assert n == 1
    rows = env.open_rows(source="jay-watch")
    assert all(r["owner"] == "aq-spam-suppressed" for r in rows)


def test_spam_source_excludes_already_suppressed_from_recount(env):
    for i in range(6):
        env.insert("jay-watch", f"row{i}")
    c = ase.db()
    assert ase.escalate_spam_sources(c) == 1
    # A fresh batch under threshold should not re-trigger since the source is
    # already ticketed (source_escalations dedup), even though more rows exist.
    env.insert("jay-watch", "row-new")
    assert ase.escalate_spam_sources(c) == 0


def test_spam_source_below_threshold_no_ticket(env):
    for i in range(3):
        env.insert("ntfy:lumen-alerts", f"row{i}")
    n = ase.escalate_spam_sources(ase.db())
    assert n == 0


def test_autoack_finding_exit_units(env, tmp_path):
    finding_list = tmp_path / "guard-finding-exit.list"
    finding_list.write_text("lumen-budget-alert.service\nwq-drift-guard.service\n")
    ase.FINDING_EXIT_LIST = str(finding_list)

    env.insert("failed-unit", "lumen-budget-alert.service")
    env.insert("failed-unit", "lumen-budget-alert.service")
    env.insert("failed-unit", "some-other.service")

    n = ase.autoack_finding_exit(ase.db())
    assert n == 2
    acked = env.open_rows(source="failed-unit", key="lumen-budget-alert.service", status="acked")
    assert len(acked) == 2
    still_open = env.open_rows(source="failed-unit", key="some-other.service", status="open")
    assert len(still_open) == 1


def test_finding_exit_noise_never_inflates_failed_unit_ticket(env, tmp_path):
    """Full run() ordering: autoack runs first, so a finding-exit guard's rows never
    reach the failed-unit threshold count as open triage-worthy rows."""
    finding_list = tmp_path / "guard-finding-exit.list"
    finding_list.write_text("primary-tree-guard.service\n")
    ase.FINDING_EXIT_LIST = str(finding_list)

    for _ in range(5):
        env.insert("failed-unit", "primary-tree-guard.service")

    c = ase.db()
    n_acked = ase.autoack_finding_exit(c)
    n_unit_tickets = ase.escalate_failed_units(c)
    assert n_acked == 5
    assert n_unit_tickets == 0  # no rows left open to escalate
