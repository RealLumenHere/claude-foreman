#!/usr/bin/env python3
"""wq#4135 — retarget_rung against the committed live-shape fixture from wq#4102.

The fixture predates the wq#4102 apply, so it has the pre-alias shape
(`lumen-lfm2-1b: [cerebras-llama]`, no `lumen/fast` key). The second case runs
alias_fallbacks first, reproducing the exact live sequence: alias chains added,
then the dead rung swapped in both the group's and the alias's chain.
"""
import sys
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "wq4102-litellm-alias-fallbacks"))
import alias_fallbacks  # noqa: E402
import retarget_rung  # noqa: E402

FIXTURE = (HERE.parent.parent / "wq4102-litellm-alias-fallbacks" / "tests" /
           "fixtures" / "live_router_settings_20261017.yaml")


def chains(text, kind="fallbacks"):
    cfg = yaml.safe_load(text)
    return {k: v for e in cfg["router_settings"][kind] for k, v in e.items()}


class TestRetarget(unittest.TestCase):
    def setUp(self):
        self.text = FIXTURE.read_text()

    def test_fixture_replaces_dead_rung(self):
        new, notes = retarget_rung.build(self.text, "cerebras-llama", "openrouter-free")
        self.assertEqual(chains(new)["lumen-lfm2-1b"], ["openrouter-free"])
        self.assertEqual(len(notes), 1)
        # untouched chains stay untouched, byte-for-byte outside the edit
        self.assertEqual(chains(new)["deepseek-v4-pro"], ["openrouter-deepseek-pro"])
        self.assertEqual(chains(new, "context_window_fallbacks")["lumen-lfm2-1b"],
                         ["deepseek-v4-flash"])

    def test_live_sequence_alias_then_retarget(self):
        after_alias, _, _ = alias_fallbacks.build(self.text)
        self.assertEqual(chains(after_alias)["lumen/fast"], ["cerebras-llama"])
        new, notes = retarget_rung.build(after_alias, "cerebras-llama", "openrouter-free")
        got = chains(new)
        self.assertEqual(got["lumen-lfm2-1b"], ["openrouter-free"])
        self.assertEqual(got["lumen/fast"], ["openrouter-free"])
        self.assertEqual(len(notes), 2)

    def test_idempotent(self):
        new, _ = retarget_rung.build(self.text, "cerebras-llama", "openrouter-free")
        again, notes = retarget_rung.build(new, "cerebras-llama", "openrouter-free")
        self.assertEqual(again, new)
        self.assertEqual(notes, [])

    def test_dedupe_when_replacement_already_in_chain(self):
        text = self.text.replace("  - lumen-lfm2-1b:\n    - cerebras-llama\n",
                                 "  - lumen-lfm2-1b:\n    - cerebras-llama\n    - openrouter-free\n")
        new, _ = retarget_rung.build(text, "cerebras-llama", "openrouter-free")
        self.assertEqual(chains(new)["lumen-lfm2-1b"], ["openrouter-free"])

    def test_check_mode(self):
        cfg = yaml.safe_load(self.text)
        _, notes = retarget_rung.plan(cfg, "cerebras-llama", "x-GONE")
        self.assertEqual(len(notes), 1)
        new, _ = retarget_rung.build(self.text, "cerebras-llama", "openrouter-free")
        _, clean = retarget_rung.plan(yaml.safe_load(new), "cerebras-llama", "x-GONE")
        self.assertEqual(clean, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
