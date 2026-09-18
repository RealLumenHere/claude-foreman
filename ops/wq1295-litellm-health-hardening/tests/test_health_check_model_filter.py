#!/usr/bin/env python3
"""wq#1295 — tests for health_check_model_filter's pure decision functions.
Runs standalone, no litellm install needed:
python3 ops/wq1295-litellm-health-hardening/tests/test_health_check_model_filter.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from health_check_model_filter import is_capped_out, render_capped_list


class TestIsCappedOut(unittest.TestCase):
    def test_opus_model_name_capped(self):
        self.assertTrue(is_capped_out("lumen-opus", "anthropic/claude-opus-4"))

    def test_opus_in_litellm_model_capped(self):
        self.assertTrue(is_capped_out("premium-tier", "anthropic/claude-opus-4"))

    def test_ollama_prefix_capped(self):
        self.assertTrue(is_capped_out("qwen36-local", "ollama/qwen36"))

    def test_local_prefix_capped(self):
        self.assertTrue(is_capped_out("local-fallback", "local/glm"))

    def test_cheap_cloud_model_not_capped(self):
        self.assertFalse(is_capped_out("glm-5.3-flash", "openai/glm-5.3-flash"))

    def test_case_insensitive(self):
        self.assertTrue(is_capped_out("Claude-OPUS", ""))


class TestRenderCappedList(unittest.TestCase):
    def test_mixed_roster_keeps_only_uncapped(self):
        model_list = [
            {"model_name": "glm-5.3-flash", "litellm_params": {"model": "openai/glm-5.3-flash"}},
            {"model_name": "lumen-opus", "litellm_params": {"model": "anthropic/claude-opus-4"}},
            {"model_name": "qwen36-local", "litellm_params": {"model": "ollama/qwen36"}},
            {"model_name": "deepseek-v4-flash", "litellm_params": {"model": "deepseek/deepseek-v4-flash"}},
        ]
        self.assertEqual(
            render_capped_list(model_list),
            ["glm-5.3-flash", "deepseek-v4-flash"],
        )

    def test_deduplicates_shared_model_name(self):
        model_list = [
            {"model_name": "glm-5.3-flash", "litellm_params": {"model": "openai/glm-5.3-flash"}},
            {"model_name": "glm-5.3-flash", "litellm_params": {"model": "openrouter/glm-5.3-flash"}},
        ]
        self.assertEqual(render_capped_list(model_list), ["glm-5.3-flash"])

    def test_empty_model_list(self):
        self.assertEqual(render_capped_list([]), [])


if __name__ == "__main__":
    unittest.main()
