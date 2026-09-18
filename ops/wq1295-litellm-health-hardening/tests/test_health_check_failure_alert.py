#!/usr/bin/env python3
"""wq#1295 — tests for health_check_failure_alert's pure decision functions.
Runs standalone, no litellm install or live ntfy needed:
python3 ops/wq1295-litellm-health-hardening/tests/test_health_check_failure_alert.py"""
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from health_check_failure_alert import (
    should_alert, handle_failure_event, extract_model_name, extract_error_reason,
)


class TestShouldAlert(unittest.TestCase):
    def test_first_breach_always_alerts(self):
        self.assertTrue(should_alert({}, "primary", now=1000.0, cooldown_s=1800))

    def test_within_cooldown_suppressed(self):
        state = {"primary": 1000.0}
        self.assertFalse(should_alert(state, "primary", now=1000.0 + 60, cooldown_s=1800))

    def test_after_cooldown_alerts_again(self):
        state = {"primary": 1000.0}
        self.assertTrue(should_alert(state, "primary", now=1000.0 + 1801, cooldown_s=1800))


class TestExtractModelName(unittest.TestCase):
    def test_prefers_model_group_metadata(self):
        kwargs = {"litellm_params": {"metadata": {"model_group": "qwen36-local"}}, "model": "ollama/qwen36"}
        self.assertEqual(extract_model_name(kwargs), "qwen36-local")

    def test_falls_back_to_model(self):
        kwargs = {"model": "ollama/qwen36"}
        self.assertEqual(extract_model_name(kwargs), "ollama/qwen36")

    def test_missing_everything(self):
        self.assertEqual(extract_model_name({}), "")


class TestExtractErrorReason(unittest.TestCase):
    def test_uses_exception_if_present(self):
        kwargs = {"exception": ValueError("upstream 503")}
        self.assertIn("upstream 503", extract_error_reason(kwargs))

    def test_falls_back_to_unknown(self):
        self.assertEqual(extract_error_reason({}), "unknown error")


class TestHandleFailureEvent(unittest.TestCase):
    def setUp(self):
        os.environ["HEALTH_CHECK_ALERT_STATE_DIR"] = "/tmp/wq1295-test-state"
        os.environ["HEALTH_CHECK_ALERT_NTFY"] = "/bin/true"  # no real alert fired
        shutil.rmtree("/tmp/wq1295-test-state", ignore_errors=True)

    def test_no_model_name_never_alerts(self):
        self.assertFalse(handle_failure_event("", "timeout"))

    def test_first_failure_alerts_once_then_cools_down(self):
        self.assertTrue(handle_failure_event("qwen36-local", "timeout"))
        self.assertFalse(handle_failure_event("qwen36-local", "timeout"))

    def test_different_models_alert_independently(self):
        self.assertTrue(handle_failure_event("qwen36-local", "timeout"))
        self.assertTrue(handle_failure_event("glm-5.3-flash", "503"))


if __name__ == "__main__":
    unittest.main()
