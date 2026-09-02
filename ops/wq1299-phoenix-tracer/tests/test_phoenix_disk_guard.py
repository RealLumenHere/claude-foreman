#!/usr/bin/env python3
"""wq#1299 — tests for phoenix_disk_guard's purge + disk-alert logic.
Runs standalone, no live Phoenix/ntfy required:
python3 ops/wq1299-phoenix-tracer/tests/test_phoenix_disk_guard.py"""
import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
import phoenix_disk_guard as pdg


def _make_db(path, with_spans=True, ts_as_text=False):
    conn = sqlite3.connect(path)
    if with_spans:
        conn.execute("CREATE TABLE spans (id INTEGER PRIMARY KEY, start_time REAL)")
        now = time.time()
        old = now - 30 * 86400
        recent = now - 1 * 86400
        if ts_as_text:
            conn.execute("CREATE TABLE spans2 (id INTEGER PRIMARY KEY, start_time TEXT)")
        rows = [old, recent]
        conn.executemany("INSERT INTO spans (start_time) VALUES (?)", [(r,) for r in rows])
    else:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


class TestPurgeOldRows(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "phoenix.db")

    def test_purges_only_rows_older_than_retention(self):
        _make_db(self.db_path)
        result = pdg.purge_old_rows(self.db_path, retention_days=14)
        self.assertEqual(result["spans"], 1)  # only the 30-day-old row
        conn = sqlite3.connect(self.db_path)
        remaining = conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
        conn.close()
        self.assertEqual(remaining, 1)

    def test_dry_run_counts_but_does_not_delete(self):
        _make_db(self.db_path)
        result = pdg.purge_old_rows(self.db_path, retention_days=14, dry_run=True)
        self.assertEqual(result["spans"], 1)
        conn = sqlite3.connect(self.db_path)
        remaining = conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
        conn.close()
        self.assertEqual(remaining, 2, "dry-run must not delete anything")

    def test_schema_mismatch_no_ops_instead_of_guessing(self):
        _make_db(self.db_path, with_spans=False)
        result = pdg.purge_old_rows(self.db_path, retention_days=14)
        self.assertIsNone(result["spans"])  # table missing -> explicit skip, not KeyError/crash
        self.assertIsNone(result["traces"])

    def test_missing_db_file_returns_empty(self):
        result = pdg.purge_old_rows(os.path.join(self.tmpdir, "nope.db"), retention_days=14)
        self.assertEqual(result, {})


class TestDbSizeBytes(unittest.TestCase):
    def test_sums_main_wal_shm(self):
        tmpdir = tempfile.mkdtemp()
        base = os.path.join(tmpdir, "phoenix.db")
        with open(base, "wb") as f:
            f.write(b"x" * 100)
        with open(base + "-wal", "wb") as f:
            f.write(b"y" * 50)
        self.assertEqual(pdg.db_size_bytes(base), 150)

    def test_missing_files_count_as_zero(self):
        tmpdir = tempfile.mkdtemp()
        self.assertEqual(pdg.db_size_bytes(os.path.join(tmpdir, "nope.db")), 0)


class TestCheckEdgeTriggering(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "phoenix.db")
        self.state_path = os.path.join(self.tmpdir, "state.json")
        _make_db(self.db_path, with_spans=False)  # avoid purge noise for size math
        with open(self.db_path, "ab") as f:
            f.write(b"z" * 1000)
        self.alerts = []
        self._orig_ntfy = pdg.ntfy
        pdg.ntfy = lambda title, message, priority="default": self.alerts.append(
            (title, message, priority)
        )

    def tearDown(self):
        pdg.ntfy = self._orig_ntfy

    def test_first_breach_alerts_once(self):
        pdg.check(self.db_path, retention_days=14, max_bytes=500, state_path=self.state_path)
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0][2], "high")

    def test_repeated_breach_does_not_realert(self):
        pdg.check(self.db_path, retention_days=14, max_bytes=500, state_path=self.state_path)
        pdg.check(self.db_path, retention_days=14, max_bytes=500, state_path=self.state_path)
        self.assertEqual(len(self.alerts), 1, "edge-triggered: second breach must not re-alert")

    def test_clear_after_breach_sends_recovery_notice(self):
        pdg.check(self.db_path, retention_days=14, max_bytes=500, state_path=self.state_path)
        pdg.check(self.db_path, retention_days=14, max_bytes=10_000_000, state_path=self.state_path)
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("under cap", self.alerts[1][1])

    def test_never_breached_no_alert(self):
        pdg.check(self.db_path, retention_days=14, max_bytes=10_000_000, state_path=self.state_path)
        self.assertEqual(self.alerts, [])

    def test_dry_run_does_not_persist_state(self):
        pdg.check(self.db_path, retention_days=14, max_bytes=500, state_path=self.state_path, dry_run=True)
        self.assertFalse(os.path.exists(self.state_path))


if __name__ == "__main__":
    unittest.main()
