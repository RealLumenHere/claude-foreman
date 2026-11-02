#!/usr/bin/env python3
"""wq#4808 — add_fallback against the committed live-shape fixture from wq#4102.

The fixture has no `gemini-flash` entry and already has
`deepseek-v4-flash: [openrouter-free]`, so it exercises both the add path
and the chain-depth claim (gemini-flash -> deepseek-v4-flash ->
openrouter-free, depth 2, no cycle) against real committed data rather than
an invented shape.
"""
import sys
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "wq4102-litellm-alias-fallbacks"))
import add_fallback  # noqa: E402

FIXTURE = (HERE.parent.parent / "wq4102-litellm-alias-fallbacks" / "tests" /
           "fixtures" / "live_router_settings_20261017.yaml")


def chains(text, kind="fallbacks"):
    cfg = yaml.safe_load(text)
    return {k: v for e in cfg["router_settings"][kind] for k, v in e.items()}


class TestAddFallback(unittest.TestCase):
    def setUp(self):
        self.text = FIXTURE.read_text()

    def test_fixture_has_no_gemini_flash_entry(self):
        self.assertNotIn("gemini-flash", chains(self.text))

    def test_adds_entry(self):
        new, notes = add_fallback.build(self.text, "gemini-flash", "deepseek-v4-flash")
        self.assertEqual(chains(new)["gemini-flash"], ["deepseek-v4-flash"])
        self.assertEqual(len(notes), 1)
        # untouched chains stay untouched, byte-for-byte outside the edit
        self.assertEqual(chains(new)["deepseek-v4-pro"], ["openrouter-deepseek-pro"])
        self.assertEqual(chains(new)["lumen-lfm2-1b"], ["cerebras-llama"])

    def test_chain_depth_two_no_cycle(self):
        new, _ = add_fallback.build(self.text, "gemini-flash", "deepseek-v4-flash")
        groups = chains(new)
        seen = ["gemini-flash"]
        cur = "gemini-flash"
        while cur in groups:
            nxt = groups[cur][0]
            self.assertNotIn(nxt, seen, "cycle in fallback chain")
            seen.append(nxt)
            cur = nxt
        self.assertEqual(seen, ["gemini-flash", "deepseek-v4-flash", "openrouter-free"])
        self.assertEqual(len(seen) - 1, 2)  # two hops to the terminal rung

    def test_idempotent(self):
        new, _ = add_fallback.build(self.text, "gemini-flash", "deepseek-v4-flash")
        again, notes = add_fallback.build(new, "gemini-flash", "deepseek-v4-flash")
        self.assertEqual(again, new)
        self.assertEqual(len(notes), 1)
        self.assertIn("already has", notes[0])

    def test_existing_group_left_alone_not_duplicated(self):
        # deepseek-v4-flash already has an explicit entry; don't overwrite it.
        new, notes = add_fallback.build(self.text, "deepseek-v4-flash", "openrouter-free")
        self.assertEqual(new, self.text)
        self.assertIn("already has", notes[0])

    def test_check_mode(self):
        cfg = yaml.safe_load(self.text)
        self.assertNotIn("gemini-flash", add_fallback.chain_index(
            cfg["router_settings"]["fallbacks"]))
        new, _ = add_fallback.build(self.text, "gemini-flash", "deepseek-v4-flash")
        cfg2 = yaml.safe_load(new)
        self.assertIn("gemini-flash", add_fallback.chain_index(
            cfg2["router_settings"]["fallbacks"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
