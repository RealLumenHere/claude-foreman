#!/usr/bin/env python3
"""wq#1617 — tests for merge_litellm_fragment. Runs standalone against
synthetic fixtures, no live config or PyYAML-backed file needed:
python3 ops/wq1617-litellm-fragment-merge/tests/test_merge_litellm_fragment.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from merge_litellm_fragment import find_dropped_pricing, merge_fragment, main


def _entry(name, model_info=None, litellm_params=None):
    return {
        "model_name": name,
        "litellm_params": litellm_params or {"model": f"openrouter/{name}"},
        "model_info": model_info or {},
    }


class TestFindDroppedPricing(unittest.TestCase):
    def test_wholesale_stale_copy_drops_pricing(self):
        # the wq#1617 shape: live has glm-5.3-flash pricing (wq#1591), the
        # stale staged file predates it and has none.
        live = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        staged = {"model_list": [_entry("glm-5.3-flash", {})]}
        dropped = find_dropped_pricing(live, staged)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["model_name"], "glm-5.3-flash")
        self.assertIn("input_cost_per_token", dropped[0]["dropped_keys"])

    def test_model_missing_entirely_from_candidate_counts_as_dropped(self):
        live = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        staged = {"model_list": [_entry("minimax-m3", {})]}
        dropped = find_dropped_pricing(live, staged)
        self.assertEqual(dropped[0]["model_name"], "glm-5.3-flash")

    def test_candidate_preserving_pricing_is_clean(self):
        live = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        staged = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        self.assertEqual(find_dropped_pricing(live, staged), [])

    def test_model_with_no_pricing_in_live_is_not_flagged(self):
        live = {"model_list": [_entry("minimax-m3", {})]}
        staged = {"model_list": []}
        self.assertEqual(find_dropped_pricing(live, staged), [])


class TestMergeFragment(unittest.TestCase):
    def test_merge_adds_pricing_without_touching_other_keys(self):
        live = {
            "model_list": [
                _entry(
                    "glm-5.3-flash",
                    model_info={"mode": "chat"},
                    litellm_params={"model": "openrouter/glm-5.3-flash", "api_key": "os.environ/OR_KEY"},
                )
            ]
        }
        fragment = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        merged = merge_fragment(live, fragment)
        entry = merged["model_list"][0]
        self.assertEqual(entry["model_info"]["mode"], "chat")
        self.assertEqual(entry["model_info"]["input_cost_per_token"], 1.5e-7)
        self.assertEqual(entry["litellm_params"]["api_key"], "os.environ/OR_KEY")

    def test_merge_appends_new_model_not_in_live(self):
        live = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        fragment = {"model_list": [_entry("minimax-m3", {"mode": "chat"})]}
        merged = merge_fragment(live, fragment)
        names = [e["model_name"] for e in merged["model_list"]]
        self.assertIn("glm-5.3-flash", names)
        self.assertIn("minimax-m3", names)

    def test_merge_allows_unrelated_new_model_with_no_pricing(self):
        live = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        fragment = {"model_list": [_entry("minimax-m3", {})]}
        # minimax-m3 isn't in live at all, so this one is safe (no erasure).
        merge_fragment(live, fragment)  # should not raise

    def test_merge_refuses_when_fragment_explicitly_nulls_existing_pricing(self):
        live = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        fragment = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": None})]}
        with self.assertRaises(ValueError):
            merge_fragment(live, fragment)

    def test_merge_never_silently_loses_a_pricing_key(self):
        live = {
            "model_list": [
                _entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7, "output_cost_per_token": 5e-7})
            ]
        }
        # fragment only sets output pricing, but on the SAME model — since
        # merge_fragment does dict.update on model_info, input pricing
        # survives; assert that explicitly (the guarantee the tool exists for).
        fragment = {"model_list": [_entry("glm-5.3-flash", {"output_cost_per_token": 6e-7})]}
        merged = merge_fragment(live, fragment)
        info = merged["model_list"][0]["model_info"]
        self.assertEqual(info["input_cost_per_token"], 1.5e-7)
        self.assertEqual(info["output_cost_per_token"], 6e-7)


class TestMainCLI(unittest.TestCase):
    def _write_yaml(self, tmp_path, config):
        import yaml
        with open(tmp_path, "w") as f:
            yaml.safe_dump(config, f)

    def test_check_exits_1_on_the_minimax_m3_shape(self):
        import tempfile
        live = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        staged = {"model_list": [_entry("glm-5.3-flash", {}), _entry("minimax-m3", {})]}
        with tempfile.TemporaryDirectory() as d:
            live_path = os.path.join(d, "live.yaml")
            staged_path = os.path.join(d, "staged.yaml")
            self._write_yaml(live_path, live)
            self._write_yaml(staged_path, staged)
            self.assertEqual(main(["check", live_path, staged_path]), 1)

    def test_check_exits_0_when_safe(self):
        import tempfile
        live = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        safe = {"model_list": [_entry("glm-5.3-flash", {"input_cost_per_token": 1.5e-7})]}
        with tempfile.TemporaryDirectory() as d:
            live_path = os.path.join(d, "live.yaml")
            safe_path = os.path.join(d, "safe.yaml")
            self._write_yaml(live_path, live)
            self._write_yaml(safe_path, safe)
            self.assertEqual(main(["check", live_path, safe_path]), 0)


if __name__ == "__main__":
    unittest.main()
