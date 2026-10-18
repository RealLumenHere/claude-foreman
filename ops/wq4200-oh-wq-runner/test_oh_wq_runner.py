#!/usr/bin/env python3
"""Pure-logic tests for oh_wq_runner — no network, no wq writes, no docker.

The acceptance-probe doctrine (wq#2630) wants probes that can fail: each test
pins at least one input from OUTSIDE the happy path the code was written
around (unknown statuses, 422-shaped path params, missing gate keys, a claim
that prints no FENCE).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oh_wq_runner as R  # noqa: E402


class FakeApi:
    """Scripted API: responses keyed by (method, path-prefix); records calls."""
    def __init__(self, script):
        self.script = script
        self.calls = []

    def call(self, method, path, body=None, timeout=30):
        self.calls.append((method, path))
        for (m, prefix), resp in self.script:
            if m == method and path.startswith(prefix):
                return resp(self.calls) if callable(resp) else resp
        return 404, "unscripted"


class TestClassify(unittest.TestCase):
    def test_finished_is_done(self):
        self.assertEqual(R.classify("finished", 0), R.DONE)

    def test_error_and_stuck_fail(self):
        self.assertEqual(R.classify("error", 0), R.FAILED)
        self.assertEqual(R.classify("stuck", 0), R.FAILED)

    def test_waiting_for_confirmation_terminal_needs_human(self):
        self.assertEqual(R.classify("waiting_for_confirmation", 0), R.NEEDS_HUMAN)

    def test_idle_needs_confirmation_streak(self):
        self.assertEqual(R.classify("idle", 1), R.RUNNING)       # startup race guard
        self.assertEqual(R.classify("idle", R.IDLE_CONFIRM_POLLS), R.NEEDS_HUMAN)

    def test_unknown_status_keeps_running_not_done(self):
        # outside-population probe: a status the design never listed must not
        # be read as success
        self.assertEqual(R.classify("deleting", 0), R.RUNNING)


class TestBounds(unittest.TestCase):
    def test_deadline_breach(self):
        self.assertIn("wall-clock", R.breach(1201, 1200, 0.01, 1.0))

    def test_cost_breach_from_accumulated_cost(self):
        self.assertIn("cost cap", R.breach(10, 1200, 0.51, 0.50))

    def test_no_breach(self):
        self.assertIsNone(R.breach(10, 1200, 0.01, 0.50))

    def test_unknown_cost_never_breaches_cost(self):
        self.assertIsNone(R.breach(10, 1200, None, 0.50))

    def test_extract_cost_tolerates_junk(self):
        self.assertEqual(R.extract_cost({"metrics": {"accumulated_cost": "0.12"}}), 0.12)
        self.assertIsNone(R.extract_cost({"metrics": {}}))
        self.assertIsNone(R.extract_cost({"metrics": {"accumulated_cost": "n/a"}}))


class TestReap(unittest.TestCase):
    def test_reap_uses_query_param_not_path(self):
        # the bare path form returns 422 live (design §3.2) — the URL must
        # carry sandbox_id as a query param
        url = R.reap_url("abc/123")
        self.assertTrue(url.startswith("/api/v1/sandboxes?sandbox_id="))
        self.assertNotIn("abc/123", url)  # quoted, not raw path-joined

    def test_run_task_reaps_in_finally_even_on_failure(self):
        api = FakeApi([
            (("POST", "/api/v1/app-conversations"),
             (200, {"id": "t1", "app_conversation_id": "c1", "sandbox_id": "s1"})),
            (("GET", "/api/v1/app-conversations?ids="),
             (200, [{"execution_status": "error", "sandbox_id": "s1",
                     "metrics": {"accumulated_cost": 0.02}}])),
            (("GET", "/api/v1/conversation/"), (200, {"items": [], "next_page_id": None})),
            (("DELETE", "/api/v1/sandboxes?sandbox_id=s1"), (200, None)),
        ])
        with tempfile.TemporaryDirectory() as d:
            res = R.run_task(api, "p", "t", d, max_seconds=60, max_cost=1.0,
                             _sleep=lambda s: None)
        self.assertEqual(res["final"], R.FAILED)
        self.assertEqual(res["reap_status"], 200)
        self.assertIn(("DELETE", "/api/v1/sandboxes?sandbox_id=s1"), api.calls)

    def test_run_task_deadline_breach_reaps_and_fails(self):
        clock = {"t": 0}

        def fake_sleep(s):
            clock["t"] += 10_000  # blow past any deadline between polls
        api = FakeApi([
            (("POST", "/api/v1/app-conversations"),
             (200, {"id": "t1", "app_conversation_id": "c1", "sandbox_id": "s2"})),
            (("GET", "/api/v1/app-conversations?ids="),
             (200, [{"execution_status": "running", "sandbox_id": "s2",
                     "metrics": {"accumulated_cost": 0.01}}])),
            (("GET", "/api/v1/conversation/"), (200, {"items": [], "next_page_id": None})),
            (("DELETE", "/api/v1/sandboxes?sandbox_id=s2"), (200, None)),
        ])
        real_time = R.time.time
        t0 = real_time()
        R.time.time = lambda: t0 + clock["t"]
        try:
            with tempfile.TemporaryDirectory() as d:
                res = R.run_task(api, "p", "t", d, max_seconds=1200, max_cost=1.0,
                                 _sleep=fake_sleep)
        finally:
            R.time.time = real_time
        self.assertEqual(res["final"], R.FAILED)
        self.assertIn("wall-clock", res["reason"])
        self.assertEqual(res["reap_status"], 200)


class TestScrub(unittest.TestCase):
    def test_session_api_key_redacted_at_any_depth(self):
        o = {"a": [{"session_api_key": "x", "keep": 1}]}
        self.assertEqual(R.scrub(o), {"a": [{"session_api_key": "<redacted>", "keep": 1}]})


class TestFence(unittest.TestCase):
    def test_parse_fence(self):
        self.assertEqual(R.parse_fence("claimed wq#7\nFENCE=42\n"), 42)

    def test_no_fence_is_none_not_zero(self):
        # outside-population probe: output without the line must not yield a
        # fence the runner would then pass to done/block
        self.assertIsNone(R.parse_fence("claimed wq#7\nfence: 42\n"))


class TestLiveGate(unittest.TestCase):
    def test_missing_file_refuses(self):
        with self.assertRaises(R.GateError):
            R.check_live_gate(path="/nonexistent/live-gate.json")

    def test_incomplete_gate_refuses(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            # (a)-(f) attested, (g) and wq4152_item6 missing -> still refused
            json.dump({k: "evidence" for k in ("a", "b", "c", "d", "e", "f")}, f)
        try:
            with self.assertRaises(R.GateError) as cm:
                R.check_live_gate(path=f.name)
            self.assertIn("wq4152_item6", str(cm.exception))
        finally:
            os.unlink(f.name)

    def test_blank_evidence_refuses(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({k: ("  " if k == "g" else "ok") for k in R.LIVE_GATE_KEYS}, f)
        try:
            with self.assertRaises(R.GateError):
                R.check_live_gate(path=f.name)
        finally:
            os.unlink(f.name)

    def test_full_gate_passes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({k: "run ref + result" for k in R.LIVE_GATE_KEYS}, f)
        try:
            self.assertTrue(R.check_live_gate(path=f.name))
        finally:
            os.unlink(f.name)


class TestHostVerify(unittest.TestCase):
    def test_no_command_fails_closed(self):
        ok, why = R.host_verify(None, "/tmp")
        self.assertFalse(ok)
        self.assertIn("fail closed", why)

    def test_command_rc_decides(self):
        ok, _ = R.host_verify("test -d {artifacts}", tempfile.gettempdir())
        self.assertTrue(ok)
        ok, _ = R.host_verify("test -d {artifacts}/does-not-exist", tempfile.gettempdir())
        self.assertFalse(ok)


class TestShadowScore(unittest.TestCase):
    def _res(self, final):
        return {"final": final, "reason": "r", "cost": 0.01, "elapsed_s": 5.0,
                "reap_status": 200}

    def test_done_ticket_agent_done_matches(self):
        row = R.score_shadow({"id": 1, "status": "done"}, self._res(R.DONE))
        self.assertTrue(row["outcome_match"])

    def test_done_ticket_agent_needs_human_mismatch(self):
        row = R.score_shadow({"id": 1, "status": "done"}, self._res(R.NEEDS_HUMAN))
        self.assertFalse(row["outcome_match"])

    def test_blocked_ticket_agent_done_is_mismatch(self):
        # outside-population probe: a recorded-blocked ticket the agent
        # "finishes" must count AGAINST the runner, not for it
        row = R.score_shadow({"id": 2, "status": "blocked"}, self._res(R.DONE))
        self.assertFalse(row["outcome_match"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
