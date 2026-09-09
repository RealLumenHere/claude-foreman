#!/usr/bin/env python3
"""wq#1573 — tests for glm53flash_price_watchdog's pure decision functions.
Runs standalone, no live OpenRouter or ntfy needed:
python3 ops/wq1573-glm53flash-eval/tests/test_glm53flash_price_watchdog.py"""
import os
import sys
import tempfile
import unittest
import pathlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from glm53flash_price_watchdog import (
    already_alerted,
    check_price_move,
    clear_state,
    find_model_pricing,
    pct_move,
    record_alert,
)


class TestPctMove(unittest.TestCase):
    def test_no_move(self):
        self.assertEqual(pct_move(0.075, 0.075), 0.0)

    def test_price_doubles(self):
        self.assertEqual(pct_move(0.075, 0.15), 100.0)

    def test_price_halves(self):
        self.assertEqual(pct_move(0.25, 0.125), -50.0)

    def test_zero_baseline_is_safe(self):
        self.assertEqual(pct_move(0.0, 5.0), 0.0)


class TestFindModelPricing(unittest.TestCase):
    def test_finds_matching_model(self):
        catalog = {
            "data": [
                {"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.000000075", "completion": "0.00000025"}},
                {"id": "other/model", "pricing": {"prompt": "1", "completion": "1"}},
            ]
        }
        usd_in, usd_out = find_model_pricing(catalog, "z-ai/glm-5.3-flash")
        self.assertAlmostEqual(usd_in, 0.075)
        self.assertAlmostEqual(usd_out, 0.25)

    def test_missing_model_returns_none(self):
        catalog = {"data": [{"id": "other/model", "pricing": {"prompt": "1", "completion": "1"}}]}
        self.assertIsNone(find_model_pricing(catalog, "z-ai/glm-5.3-flash"))

    def test_malformed_pricing_returns_none(self):
        catalog = {"data": [{"id": "z-ai/glm-5.3-flash", "pricing": {}}]}
        self.assertIsNone(find_model_pricing(catalog, "z-ai/glm-5.3-flash"))


class TestCheckPriceMove(unittest.TestCase):
    def test_within_threshold_no_breach(self):
        breach = check_price_move(0.075, 0.25, (0.08, 0.27), threshold_pct=25.0)
        self.assertIsNone(breach)

    def test_input_price_breach(self):
        breach = check_price_move(0.075, 0.25, (0.12, 0.25), threshold_pct=25.0)
        self.assertIsNotNone(breach)
        self.assertEqual(breach["reason"], "price moved beyond threshold")

    def test_output_price_breach(self):
        breach = check_price_move(0.075, 0.25, (0.075, 0.40), threshold_pct=25.0)
        self.assertIsNotNone(breach)

    def test_price_drop_also_breaches(self):
        breach = check_price_move(0.075, 0.25, (0.01, 0.25), threshold_pct=25.0)
        self.assertIsNotNone(breach)

    def test_model_vanished_is_a_breach(self):
        breach = check_price_move(0.075, 0.25, None, threshold_pct=25.0)
        self.assertIsNotNone(breach)
        self.assertEqual(breach["reason"], "model missing from OpenRouter catalog")


class TestAlertStateEdgeTrigger(unittest.TestCase):
    def test_first_breach_not_already_alerted(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = pathlib.Path(d)
            breach = {"reason": "x", "move_in_pct": 30.0, "move_out_pct": 0.0}
            self.assertFalse(already_alerted(state_dir, breach))

    def test_repeated_identical_breach_is_suppressed(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = pathlib.Path(d)
            breach = {"reason": "x", "move_in_pct": 30.0, "move_out_pct": 0.0}
            record_alert(state_dir, breach)
            self.assertTrue(already_alerted(state_dir, breach))

    def test_new_breach_shape_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = pathlib.Path(d)
            record_alert(state_dir, {"reason": "x", "move_in_pct": 30.0, "move_out_pct": 0.0})
            self.assertFalse(already_alerted(state_dir, {"reason": "x", "move_in_pct": 60.0, "move_out_pct": 0.0}))

    def test_clear_state_resets(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = pathlib.Path(d)
            breach = {"reason": "x", "move_in_pct": 30.0, "move_out_pct": 0.0}
            record_alert(state_dir, breach)
            clear_state(state_dir)
            self.assertFalse(already_alerted(state_dir, breach))


if __name__ == "__main__":
    unittest.main()
