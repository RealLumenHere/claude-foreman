#!/usr/bin/env python3
"""wq#1590 — tests for deepseek_connect_alert's pure decision functions. Runs
standalone, no litellm install or live ntfy needed:
python3 ops/wq1590-deepseek-connect-guard/tests/test_deepseek_connect_alert.py"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from deepseek_connect_alert import (
    is_connect_failure, is_deepseek_model, should_alert, prune_events,
    windowed_rate, handle_call_event,
)


class TestIsConnectFailure(unittest.TestCase):
    def test_ticket_exact_string(self):
        self.assertTrue(is_connect_failure(
            "litellm.InternalServerError DeepseekException - Cannot connect "
            "to host api.deepseek.com:443 ssl:default"
        ))

    def test_ssl_error(self):
        self.assertTrue(is_connect_failure("SSLError: [SSL] record layer failure"))

    def test_dns_failure(self):
        self.assertTrue(is_connect_failure("Temporary failure in name resolution"))

    def test_application_error_not_connect(self):
        self.assertFalse(is_connect_failure("AuthenticationError: invalid API key"))

    def test_rate_limit_not_connect(self):
        self.assertFalse(is_connect_failure("RateLimitError: 429 quota exceeded"))

    def test_empty_string(self):
        self.assertFalse(is_connect_failure(""))


class TestIsDeepseekModel(unittest.TestCase):
    def test_provider_prefixed(self):
        self.assertTrue(is_deepseek_model("deepseek/deepseek-v4-flash"))

    def test_bare_id(self):
        self.assertTrue(is_deepseek_model("deepseek-v4-flash"))

    def test_other_model_not_deepseek(self):
        self.assertFalse(is_deepseek_model("ollama/qwen36"))

    def test_empty_not_deepseek(self):
        self.assertFalse(is_deepseek_model(""))


class TestShouldAlert(unittest.TestCase):
    def test_first_breach_always_alerts(self):
        self.assertTrue(should_alert({}, "deepseek-v4-flash", now=1000.0, cooldown_s=1800))

    def test_within_cooldown_suppressed(self):
        state = {"deepseek-v4-flash": 1000.0}
        self.assertFalse(should_alert(state, "deepseek-v4-flash", now=1000.0 + 60, cooldown_s=1800))

    def test_after_cooldown_alerts_again(self):
        state = {"deepseek-v4-flash": 1000.0}
        self.assertTrue(should_alert(state, "deepseek-v4-flash", now=1000.0 + 1801, cooldown_s=1800))


class TestPruneAndRate(unittest.TestCase):
    def test_prune_drops_stale_events(self):
        events = [[0.0, True], [500.0, False], [1000.0, True]]
        pruned = prune_events(events, now=1000.0, window_s=600)
        self.assertEqual(pruned, [[500.0, False], [1000.0, True]])

    def test_windowed_rate(self):
        events = [[0.0, True], [1.0, True], [2.0, False], [3.0, False]]
        stats = windowed_rate(events)
        self.assertEqual(stats, {"total": 4, "failures": 2, "rate": 0.5})

    def test_windowed_rate_empty(self):
        self.assertEqual(windowed_rate([]), {"total": 0, "failures": 0, "rate": 0.0})


class TestHandleCallEvent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deepseek-connect-alert-test-")
        self._old_env = {
            k: os.environ.get(k) for k in (
                "DEEPSEEK_ALERT_STATE_DIR", "DEEPSEEK_ALERT_MIN_SAMPLES",
                "DEEPSEEK_ALERT_THRESHOLD", "DEEPSEEK_ALERT_COOLDOWN_MIN",
                "DEEPSEEK_ALERT_WINDOW_MIN", "DEEPSEEK_ALERT_NTFY",
            )
        }
        os.environ["DEEPSEEK_ALERT_STATE_DIR"] = self.tmp
        os.environ["DEEPSEEK_ALERT_MIN_SAMPLES"] = "4"
        os.environ["DEEPSEEK_ALERT_THRESHOLD"] = "0.5"
        os.environ["DEEPSEEK_ALERT_COOLDOWN_MIN"] = "30"
        os.environ["DEEPSEEK_ALERT_WINDOW_MIN"] = "90"
        # never actually shell out in tests
        os.environ["DEEPSEEK_ALERT_NTFY"] = "/bin/true"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_non_deepseek_model_never_tracked(self):
        for _ in range(10):
            self.assertFalse(handle_call_event("ollama/qwen36", "Cannot connect to host", now=1.0))

    def test_below_min_samples_never_alerts(self):
        alerted = [handle_call_event("deepseek/deepseek-v4-flash", "Cannot connect to host", now=float(i))
                   for i in range(3)]
        self.assertFalse(any(alerted))

    def test_below_threshold_never_alerts(self):
        # 1 failure, 3 successes -> 25% rate, below the 50% threshold
        results = []
        results.append(handle_call_event("deepseek/deepseek-v4-flash", "Cannot connect to host", now=0.0))
        for i in range(1, 4):
            results.append(handle_call_event("deepseek/deepseek-v4-flash", None, now=float(i)))
        self.assertFalse(any(results))

    def test_sustained_connect_failures_trip_the_alert(self):
        # 3 failures, 1 success -> 75% rate, at/above the 50% threshold, >= min samples
        results = []
        for i in range(3):
            results.append(handle_call_event(
                "deepseek/deepseek-v4-flash", "Cannot connect to host api.deepseek.com:443", now=float(i)))
        results.append(handle_call_event("deepseek/deepseek-v4-flash", None, now=3.0))
        self.assertTrue(results[-1])
        self.assertEqual(sum(1 for r in results if r), 1)

    def test_cooldown_suppresses_repeat_alert(self):
        for i in range(3):
            handle_call_event("deepseek/deepseek-v4-flash", "Cannot connect to host", now=float(i))
        first = handle_call_event("deepseek/deepseek-v4-flash", "Cannot connect to host", now=3.0)
        second = handle_call_event("deepseek/deepseek-v4-flash", "Cannot connect to host", now=4.0)
        self.assertTrue(first)
        self.assertFalse(second)

    def test_realigns_after_cooldown_expires(self):
        for i in range(3):
            handle_call_event("deepseek/deepseek-v4-flash", "Cannot connect to host", now=float(i))
        first = handle_call_event("deepseek/deepseek-v4-flash", "Cannot connect to host", now=3.0)
        later = handle_call_event("deepseek/deepseek-v4-flash", "Cannot connect to host", now=3.0 + 1801)
        self.assertTrue(first)
        self.assertTrue(later)


if __name__ == "__main__":
    unittest.main()
