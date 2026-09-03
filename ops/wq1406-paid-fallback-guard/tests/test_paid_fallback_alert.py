#!/usr/bin/env python3
"""wq#1406 — tests for paid_fallback_alert's pure decision functions. Runs
standalone, no litellm install or live ntfy needed:
python3 ops/wq1406-paid-fallback-guard/tests/test_paid_fallback_alert.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from paid_fallback_alert import (
    is_paid_model, is_fallback_response, should_alert, handle_success_event,
)


class TestIsPaidModel(unittest.TestCase):
    def test_deepseek_paid(self):
        self.assertTrue(is_paid_model("deepseek/deepseek-v4-flash"))

    def test_local_ollama_not_paid(self):
        self.assertFalse(is_paid_model("ollama/qwen36"))


class TestIsFallbackResponse(unittest.TestCase):
    def test_same_group_is_not_a_fallback(self):
        self.assertFalse(is_fallback_response("qwen36-local", "qwen36-local"))

    def test_different_group_is_a_fallback(self):
        self.assertTrue(is_fallback_response("qwen36-local", "deepseek-v4-flash"))

    def test_missing_fields_never_alert(self):
        self.assertFalse(is_fallback_response("", "deepseek-v4-flash"))
        self.assertFalse(is_fallback_response("qwen36-local", ""))


class TestShouldAlert(unittest.TestCase):
    def test_first_breach_always_alerts(self):
        self.assertTrue(should_alert({}, "primary", now=1000.0, cooldown_s=1800))

    def test_within_cooldown_suppressed(self):
        state = {"primary": 1000.0}
        self.assertFalse(should_alert(state, "primary", now=1000.0 + 60, cooldown_s=1800))

    def test_after_cooldown_alerts_again(self):
        state = {"primary": 1000.0}
        self.assertTrue(should_alert(state, "primary", now=1000.0 + 1801, cooldown_s=1800))


class TestHandleSuccessEvent(unittest.TestCase, ):
    def setUp(self):
        self.tmp = os.environ.get("PAID_FALLBACK_STATE_DIR")
        os.environ["PAID_FALLBACK_STATE_DIR"] = "/tmp/wq1406-test-state"
        os.environ["PAID_FALLBACK_NTFY"] = "/bin/true"  # no real alert fired
        import shutil
        shutil.rmtree("/tmp/wq1406-test-state", ignore_errors=True)

    def test_free_fallback_never_alerts(self):
        self.assertFalse(handle_success_event("primary", "backup-local", "ollama/backup"))

    def test_same_group_never_alerts(self):
        self.assertFalse(handle_success_event("primary", "primary", "deepseek/x"))

    def test_paid_fallback_alerts_once_then_cools_down(self):
        self.assertTrue(handle_success_event("primary", "deepseek-fallback", "deepseek/x"))
        # second call within cooldown: suppressed
        self.assertFalse(handle_success_event("primary", "deepseek-fallback", "deepseek/x"))


if __name__ == "__main__":
    unittest.main()
