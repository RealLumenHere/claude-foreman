#!/usr/bin/env python3
"""Tests for check_chunk_doc_invariants.py (wq#1776). No live DB, no live aq —
`measure()` takes a fake cursor and `raise_aq`/`_aq_add` are exercised with a
patched `subprocess.run` so nothing here writes to the real alert queue.
"""
import datetime
import json
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_chunk_doc_invariants as m


class FakeCursor:
    def __init__(self, results):
        self._results = list(results)
        self._last = None

    def execute(self, sql, *a):
        self._last = self._results.pop(0)

    def fetchone(self):
        return self._last

    def fetchall(self):
        return self._last


class FakeConn:
    def __init__(self, results):
        self._cur = FakeCursor(results)

    def cursor(self):
        return self._cur


class MeasureTests(unittest.TestCase):
    def test_no_drift_no_orphans_cascade_present(self):
        conn = FakeConn([
            (0, 0, 0),
            [("research_chunks_doc_id_fkey",
              "FOREIGN KEY (doc_id) REFERENCES research_docs(id) ON DELETE CASCADE")],
        ])
        counts = m.measure(conn)
        self.assertEqual(counts["i1_stale_chunks"], 0)
        self.assertEqual(counts["i2_orphan_chunks"], 0)
        self.assertTrue(counts["doc_id_fk_cascades"])

    def test_stale_chunks_and_missing_cascade(self):
        conn = FakeConn([
            (19, 19, 0),
            [("research_chunks_doc_id_fkey",
              "FOREIGN KEY (doc_id) REFERENCES research_docs(id)")],
        ])
        counts = m.measure(conn)
        self.assertEqual(counts["i1_stale_chunks"], 19)
        self.assertFalse(counts["doc_id_fk_cascades"])

    def test_no_fk_at_all(self):
        conn = FakeConn([(0, 0, 3), []])
        counts = m.measure(conn)
        self.assertEqual(counts["i2_orphan_chunks"], 3)
        self.assertFalse(counts["doc_id_fk_cascades"])


class LineTests(unittest.TestCase):
    def test_line_contains_all_counts(self):
        s = m.line({"i1_stale_chunks": 19, "i1_docs_affected": 19,
                     "i2_orphan_chunks": 0, "doc_id_fk_cascades": True})
        self.assertIn("I1 stale-chunks=19", s)
        self.assertIn("docs affected=19", s)
        self.assertIn("I2 orphan-chunks=0", s)
        self.assertIn("doc_id FK cascades=True", s)


class AlarmTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 9, 11, tzinfo=datetime.timezone.utc)
        self.base = {"i1_stale_chunks": 19, "i1_docs_affected": 19,
                     "i2_orphan_chunks": 0, "doc_id_fk_cascades": True}

    def test_first_run_no_prior_state_raises_nothing(self):
        self.assertEqual(m.raise_aq(None, self.base, self.now), [])

    def test_no_growth_raises_nothing(self):
        prev = dict(self.base)
        with unittest.mock.patch.object(m.subprocess, "run") as run:
            raised = m.raise_aq(prev, self.base, self.now)
        run.assert_not_called()
        self.assertEqual(raised, [])

    def test_i1_growth_raises_one_aq_row(self):
        prev = dict(self.base, i1_stale_chunks=5, i1_docs_affected=5)
        with unittest.mock.patch.object(m.subprocess, "run") as run:
            raised = m.raise_aq(prev, self.base, self.now)
        run.assert_called_once()
        args = run.call_args[0][0]
        self.assertEqual(args[0], m.AQ)
        self.assertEqual(args[1], "add")
        self.assertEqual(args[2], "pgvector")
        self.assertIn("i1_stale_chunks-2026-09-11", args[3])
        self.assertIn("grew by 14", args[4])
        self.assertEqual(raised, [args[3]])

    def test_i2_growth_raises(self):
        prev = dict(self.base, i2_orphan_chunks=0)
        counts = dict(self.base, i2_orphan_chunks=2)
        with unittest.mock.patch.object(m.subprocess, "run") as run:
            raised = m.raise_aq(prev, counts, self.now)
        run.assert_called_once()
        self.assertIn("i2_orphan_chunks", raised[0])

    def test_cascade_disappearing_raises(self):
        prev = dict(self.base, doc_id_fk_cascades=True)
        counts = dict(self.base, doc_id_fk_cascades=False)
        with unittest.mock.patch.object(m.subprocess, "run") as run:
            raised = m.raise_aq(prev, counts, self.now)
        run.assert_called_once()
        self.assertIn("fk-cascade-missing", raised[0])

    def test_decrease_does_not_raise(self):
        prev = dict(self.base, i1_stale_chunks=30, i1_docs_affected=30)
        with unittest.mock.patch.object(m.subprocess, "run") as run:
            raised = m.raise_aq(prev, self.base, self.now)
        run.assert_not_called()
        self.assertEqual(raised, [])

    def test_aq_add_failure_is_swallowed(self):
        with unittest.mock.patch.object(
                m.subprocess, "run", side_effect=OSError("no such file")):
            result = m._aq_add("k", "t", "m")
        self.assertIsNone(result)


class StateTests(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nested", "state.json")
            now = datetime.datetime(2026, 9, 11, tzinfo=datetime.timezone.utc)
            counts = {"i1_stale_chunks": 1, "i1_docs_affected": 1,
                      "i2_orphan_chunks": 0, "doc_id_fk_cascades": True}
            m.save_state(path, counts, now)
            loaded = m.load_state(path)
            self.assertEqual(loaded["i1_stale_chunks"], 1)
            self.assertEqual(loaded["checked_at"], now.isoformat())

    def test_missing_state_file_returns_none(self):
        self.assertIsNone(m.load_state("/nonexistent/path/state.json"))

    def test_corrupt_state_file_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                f.write("not json")
            self.assertIsNone(m.load_state(path))


class MainTests(unittest.TestCase):
    """main()-level coverage (wq#1788/wq#2152): a Postgres failure at any point —
    connect or query — must print UNAVAILABLE and return 0, never raise, so the
    daily timer and handoff verify are never taken down by an outage."""

    class _ClosableConn(FakeConn):
        def __init__(self, results):
            super().__init__(results)
            self.closed = False

        def close(self):
            self.closed = True

    class _QueryFailConn:
        def __init__(self):
            self.closed = False

        def cursor(self):
            cur = unittest.mock.Mock()
            cur.execute.side_effect = RuntimeError("relation does not exist")
            return cur

        def close(self):
            self.closed = True

    def _run_main(self, argv=None):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = m.main(argv or [])
        return rc, out.getvalue()

    def test_connect_failure_warns_and_exits_0(self):
        with unittest.mock.patch.object(
                m, "connect", side_effect=OSError("connection refused")):
            rc, out = self._run_main()
        self.assertEqual(rc, 0)
        self.assertIn("UNAVAILABLE", out)
        self.assertIn("connection refused", out)

    def test_query_failure_warns_exits_0_and_closes_conn(self):
        conn = self._QueryFailConn()
        with unittest.mock.patch.object(m, "connect", return_value=conn):
            rc, out = self._run_main()
        self.assertEqual(rc, 0)
        self.assertIn("UNAVAILABLE", out)
        self.assertIn("relation does not exist", out)
        self.assertTrue(conn.closed)

    def test_success_prints_report_and_exits_0(self):
        conn = self._ClosableConn([
            (0, 0, 0),
            [("research_chunks_doc_id_fkey",
              "FOREIGN KEY (doc_id) REFERENCES research_docs(id) ON DELETE CASCADE")],
        ])
        with unittest.mock.patch.object(m, "connect", return_value=conn):
            rc, out = self._run_main()
        self.assertEqual(rc, 0)
        self.assertIn("I1 stale-chunks=0", out)
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main()
