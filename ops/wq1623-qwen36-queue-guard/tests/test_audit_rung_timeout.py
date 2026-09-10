#!/usr/bin/env python3
"""wq#1623 — tests for audit_rung_timeout. Runs standalone, no live config
or PyYAML-backed file needed:
python3 ops/wq1623-qwen36-queue-guard/tests/test_audit_rung_timeout.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from audit_rung_timeout import MAX_CALLER_WAIT_S, targets_qwen36_isis, run_audit


class TestTargetsQwen36Isis(unittest.TestCase):
    def test_ollama_qwen36_model_string_matches(self):
        self.assertTrue(targets_qwen36_isis("ollama/qwen36", ""))

    def test_isis_api_base_matches_even_with_other_model_string(self):
        self.assertTrue(targets_qwen36_isis("openai/local", "http://100.100.129.24:11434"))

    def test_unrelated_deployment_does_not_match(self):
        self.assertFalse(targets_qwen36_isis("cerebras/oss-120b", "https://api.cerebras.ai"))

    def test_case_insensitive(self):
        self.assertTrue(targets_qwen36_isis("Ollama/Qwen36", ""))


class TestRunAudit(unittest.TestCase):
    def _config(self, model_list):
        return {"model_list": model_list}

    def test_timeout_at_ceiling_is_clean(self):
        config = self._config([
            {"model_name": "qwen36-local", "litellm_params": {
                "model": "ollama/qwen36", "timeout": MAX_CALLER_WAIT_S}},
        ])
        self.assertEqual(run_audit(config), [])

    def test_timeout_over_ceiling_is_a_violation(self):
        # the wq#1623 shape: qwen36-local carried timeout: 600 against a
        # rung where callers cancel at 40-160s.
        config = self._config([
            {"model_name": "qwen36-local", "litellm_params": {
                "model": "ollama/qwen36", "timeout": 600}},
        ])
        violations = run_audit(config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["model_name"], "qwen36-local")
        self.assertEqual(violations[0]["timeout"], 600)

    def test_qwen36_live_over_ceiling_is_also_caught(self):
        config = self._config([
            {"model_name": "qwen36-live", "litellm_params": {
                "model": "ollama/qwen36-live", "timeout": 181}},
        ])
        self.assertEqual(len(run_audit(config)), 1)

    def test_missing_timeout_is_not_flagged(self):
        # absence isn't this audit's job -- only an explicit value above
        # the ceiling is a violation.
        config = self._config([
            {"model_name": "qwen36-local", "litellm_params": {"model": "ollama/qwen36"}},
        ])
        self.assertEqual(run_audit(config), [])

    def test_unrelated_paid_deployment_is_never_flagged(self):
        config = self._config([
            {"model_name": "cerebras-oss-120b", "litellm_params": {
                "model": "cerebras/oss-120b", "timeout": 600}},
        ])
        self.assertEqual(run_audit(config), [])


if __name__ == "__main__":
    unittest.main()
