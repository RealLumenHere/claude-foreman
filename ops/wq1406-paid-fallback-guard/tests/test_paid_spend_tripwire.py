#!/usr/bin/env python3
"""wq#1406 — tests for paid_spend_tripwire's pure decision functions. Runs
standalone, no live LiteLLM proxy or ntfy needed:
python3 ops/wq1406-paid-fallback-guard/tests/test_paid_spend_tripwire.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from paid_spend_tripwire import check_thresholds, threshold_env_key, threshold_for


class TestThresholdEnvKey(unittest.TestCase):
    def test_sanitizes_model_name(self):
        self.assertEqual(
            threshold_env_key("deepseek/deepseek-v4-flash"),
            "PAID_SPEND_THRESHOLD_DEEPSEEK_DEEPSEEK_V4_FLASH",
        )


class TestThresholdFor(unittest.TestCase):
    def test_uses_default_when_no_override(self):
        self.assertEqual(threshold_for("deepseek/x", {}, default=5.0), 5.0)

    def test_uses_per_model_override(self):
        env = {"PAID_SPEND_THRESHOLD_DEEPSEEK_X": "12.5"}
        self.assertEqual(threshold_for("deepseek/x", env, default=5.0), 12.5)

    def test_ignores_malformed_override(self):
        env = {"PAID_SPEND_THRESHOLD_DEEPSEEK_X": "not-a-number"}
        self.assertEqual(threshold_for("deepseek/x", env, default=5.0), 5.0)


class TestCheckThresholds(unittest.TestCase):
    def test_paid_model_over_default_threshold_breaches(self):
        spend = {"deepseek/deepseek-v4-flash": 23.40}
        breaches = check_thresholds(spend, {"PAID_SPEND_THRESHOLD_USD": "5.00"})
        self.assertEqual(len(breaches), 1)
        self.assertEqual(breaches[0]["model"], "deepseek/deepseek-v4-flash")

    def test_free_model_never_breaches_even_if_spend_present(self):
        spend = {"ollama/qwen36": 999.0}
        self.assertEqual(check_thresholds(spend, {}), [])

    def test_under_threshold_no_breach(self):
        spend = {"deepseek/x": 1.0}
        self.assertEqual(check_thresholds(spend, {"PAID_SPEND_THRESHOLD_USD": "5.00"}), [])

    def test_per_model_override_applies(self):
        spend = {"deepseek/x": 8.0}
        env = {"PAID_SPEND_THRESHOLD_USD": "5.00", "PAID_SPEND_THRESHOLD_DEEPSEEK_X": "10.00"}
        self.assertEqual(check_thresholds(spend, env), [])


if __name__ == "__main__":
    unittest.main()
