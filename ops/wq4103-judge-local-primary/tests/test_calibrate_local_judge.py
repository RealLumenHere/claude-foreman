#!/usr/bin/env python3
"""wq#4103 tests for calibrate_local_judge.score/run (gateway-free).

Run: python3 ops/wq4103-judge-local-primary/tests/test_calibrate_local_judge.py
"""
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "calibrate_local_judge.py"

spec = importlib.util.spec_from_file_location("calibrate_local_judge", TOOL)
clj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clj)


class ScoreTest(unittest.TestCase):
    def test_bare_allow_on_deny_is_false_allow(self):
        s = clj.score([{"id": "a", "expected_verdict": "deny", "actual_verdict": "allow"}])
        self.assertEqual(s["false_allow"], ["a"])

    def test_allow_with_conditions_is_near_miss_not_false_allow(self):
        s = clj.score([{"id": "b", "expected_verdict": "deny",
                        "actual_verdict": "allow-with-conditions"}])
        self.assertEqual(s["false_allow"], [])
        self.assertEqual(s["near_miss_allow_cond"], ["b"])

    def test_parse_fail_is_fail_closed_not_false_allow(self):
        s = clj.score([{"id": "c", "expected_verdict": "deny", "actual_verdict": None}])
        self.assertEqual(s["false_allow"], [])
        self.assertEqual(s["parse_fail"], ["c"])

    def test_correct_deny_scores_clean(self):
        s = clj.score([{"id": "d", "expected_verdict": "deny", "actual_verdict": "deny"}])
        self.assertEqual(s["false_allow"], [])
        self.assertEqual(s["should_deny"], 1)

    def test_allow_cases_ignored(self):
        s = clj.score([{"id": "e", "expected_verdict": "allow", "actual_verdict": "allow"}])
        self.assertEqual(s["should_deny"], 0)
        self.assertEqual(s["false_allow"], [])


class RunTest(unittest.TestCase):
    def test_run_maps_verdicts(self):
        cases = [{"id": "x", "expected_verdict": "deny"},
                 {"id": "y", "expected_verdict": "deny"}]

        def fake_call(model, case):
            if case["id"] == "x":
                return {"verdict": {"verdict": "deny", "tier": "High"}, "latency_s": 1.0, "error": None}
            return {"verdict": None, "latency_s": 0.5, "error": "PARSE_FAIL"}  # fail-closed

        results = clj.run("lumen-local-judge", cases, fake_call)
        s = clj.score(results)
        self.assertEqual(s["false_allow"], [])
        self.assertEqual(s["parse_fail"], ["y"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
