#!/usr/bin/env python3
"""wq#1406 — tests for audit_fallback_chains. Runs standalone, no live
config or PyYAML-backed file needed:
python3 ops/wq1406-paid-fallback-guard/tests/test_audit_fallback_chains.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from audit_fallback_chains import classify_model, group_classification, run_audit


class TestClassifyModel(unittest.TestCase):
    def test_deepseek_is_paid(self):
        self.assertEqual(classify_model("deepseek/deepseek-v4-flash"), "paid")

    def test_openrouter_mistral_anthropic_openai_cerebras_are_paid(self):
        for m in [
            "openrouter/some-model", "mistral/mistral-small",
            "anthropic/claude-x", "openai/gpt-x", "cerebras/oss-120b",
        ]:
            self.assertEqual(classify_model(m), "paid", m)

    def test_ollama_local_is_free(self):
        self.assertEqual(classify_model("ollama/qwen36"), "free")

    def test_case_insensitive(self):
        self.assertEqual(classify_model("DeepSeek/deepseek-v4-flash"), "paid")


class TestRunAudit(unittest.TestCase):
    def _config(self, model_list, fallbacks):
        return {"model_list": model_list, "router_settings": {"fallbacks": fallbacks}}

    def test_free_before_paid_is_clean(self):
        model_list = [
            {"model_name": "qwen36-local", "litellm_params": {"model": "ollama/qwen36"}},
            {"model_name": "cerebras-oss-120b", "litellm_params": {"model": "cerebras/oss-120b"}},
            {"model_name": "mistral-small", "litellm_params": {"model": "mistral/mistral-small"}},
        ]
        fallbacks = [{"qwen36-local": ["cerebras-oss-120b", "mistral-small"]}]
        config = self._config(model_list, fallbacks)
        self.assertEqual(run_audit(config), [])

    def test_paid_first_is_a_violation(self):
        # the DeepSeek/LPP runaway shape: primary paid-adjacent rung has NO
        # free fallback ahead of a second paid rung.
        model_list = [
            {"model_name": "cerebras-primary", "litellm_params": {"model": "cerebras/oss-120b"}},
            {"model_name": "deepseek-fallback", "litellm_params": {"model": "deepseek/deepseek-v4-flash"}},
        ]
        fallbacks = [{"cerebras-primary": ["deepseek-fallback"]}]
        config = self._config(model_list, fallbacks)
        violations = run_audit(config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["paid_rung"], "deepseek-fallback")

    def test_free_rung_after_paid_does_not_clear_the_violation(self):
        model_list = [
            {"model_name": "primary", "litellm_params": {"model": "ollama/x"}},
            {"model_name": "paid-rung", "litellm_params": {"model": "deepseek/x"}},
            {"model_name": "free-rung", "litellm_params": {"model": "ollama/y"}},
        ]
        fallbacks = [{"primary": ["paid-rung", "free-rung"]}]
        config = self._config(model_list, fallbacks)
        violations = run_audit(config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["paid_rung"], "paid-rung")

    def test_mixed_group_any_paid_deployment_marks_group_paid(self):
        model_list = [
            {"model_name": "shared-alias", "litellm_params": {"model": "ollama/x"}},
            {"model_name": "shared-alias", "litellm_params": {"model": "deepseek/x"}},
        ]
        self.assertEqual(group_classification({"model_list": model_list})["shared-alias"], "paid")

    def test_unknown_target_is_not_treated_as_free(self):
        model_list = [
            {"model_name": "primary", "litellm_params": {"model": "ollama/x"}},
        ]
        # 'typo-target' has no model_list entry at all (dangling fallback,
        # ISSUE-LOG #9's failure class) — must not silently pass as free.
        fallbacks = [{"primary": ["typo-target"]}]
        config = self._config(model_list, fallbacks)
        self.assertEqual(run_audit(config), [])  # unknown != paid, so no false alarm either
        from audit_fallback_chains import group_classification
        self.assertNotIn("typo-target", group_classification(config))


if __name__ == "__main__":
    unittest.main()
