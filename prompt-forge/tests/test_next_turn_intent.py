#!/usr/bin/env python3
"""Tests for next_turn_intent.py (wq#2521): history loading and the
recency-weighted category/filter prediction. Stdlib unittest; no LLM
involved — the module is deliberately deterministic."""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import next_turn_intent


def _write_turn(hist_dir, name, category, filters):
    (hist_dir / f"{name}.json").write_text(json.dumps(
        {"category": category, "filters": filters}))


class TestLoadHistory(unittest.TestCase):
    def test_missing_dir_returns_empty(self):
        with TemporaryDirectory() as td:
            self.assertEqual(next_turn_intent.load_history(Path(td) / "nope"), [])

    def test_loads_in_name_order_and_skips_bad_json(self):
        with TemporaryDirectory() as td:
            hist_dir = Path(td) / "history"
            hist_dir.mkdir()
            _write_turn(hist_dir, "20260101_000001_a", "research", ["web_research"])
            _write_turn(hist_dir, "20260101_000002_b", "project", [])
            (hist_dir / "20260101_000003_c.json").write_text("{not json")
            entries = next_turn_intent.load_history(td)
            self.assertEqual([e["category"] for e in entries], ["research", "project"])

    def test_skips_valid_json_of_the_wrong_shape(self):
        """wq#2522/wq#2523: a file holding valid JSON that isn't an object
        parses fine, so the json.loads except clause never sees it; without a
        shape check it reaches _is_research_turn and raises AttributeError."""
        with TemporaryDirectory() as td:
            hist_dir = Path(td) / "history"
            hist_dir.mkdir()
            _write_turn(hist_dir, "20260101_000001_a", "research", ["web_research"])
            _write_turn(hist_dir, "20260101_000002_b", "research", ["web_research"])
            (hist_dir / "20260101_000003_c.json").write_text("[1, 2, 3]")
            (hist_dir / "20260101_000004_d.json").write_text("null")
            (hist_dir / "20260101_000005_e.json").write_text('"a string"')
            entries = next_turn_intent.load_history(td)
            self.assertEqual([e["category"] for e in entries],
                             ["research", "research"])
            result = next_turn_intent.predict_next_intent(entries)
            self.assertEqual(result["predicted_category"], "research")

    def test_limit_keeps_most_recent(self):
        with TemporaryDirectory() as td:
            hist_dir = Path(td) / "history"
            hist_dir.mkdir()
            for i in range(5):
                _write_turn(hist_dir, f"2026010{i}", "research", [])
            entries = next_turn_intent.load_history(td, limit=2)
            self.assertEqual(len(entries), 2)


class TestPredictNextIntent(unittest.TestCase):
    def test_insufficient_research_turns_predicts_nothing(self):
        history = [{"category": "project", "filters": []}]
        result = next_turn_intent.predict_next_intent(history)
        self.assertIsNone(result["predicted_category"])
        self.assertEqual(result["predicted_filters"], [])
        self.assertEqual(result["confidence"], 0.0)

    def test_predicts_dominant_category_and_filters(self):
        history = [
            {"category": "project", "filters": []},
            {"category": "research", "filters": ["web_research", "hardware"]},
            {"category": "research", "filters": ["web_research"]},
            {"category": "research", "filters": ["web_research", "continuation"]},
        ]
        result = next_turn_intent.predict_next_intent(history)
        self.assertEqual(result["predicted_category"], "research")
        self.assertIn("web_research", result["predicted_filters"])
        self.assertEqual(result["based_on"], 3)
        self.assertGreater(result["confidence"], 0.0)

    def test_recent_turns_outweigh_older_ones(self):
        history = ([{"category": "research", "filters": ["hardware"]}] * 3
                   + [{"category": "research", "filters": ["web_research"]}])
        result = next_turn_intent.predict_next_intent(history)
        self.assertEqual(result["predicted_filters"][0], "web_research")


if __name__ == "__main__":
    unittest.main()
