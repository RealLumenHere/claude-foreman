#!/usr/bin/env python3
"""wq#4102 — tests for alias_fallbacks. Runs standalone:
python3 ops/wq4102-litellm-alias-fallbacks/tests/test_alias_fallbacks.py"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from alias_fallbacks import build, main, plan  # noqa: E402

FIXTURE = HERE / "fixtures" / "live_router_settings_20261017.yaml"


def fallback_lookup(entries, model_group):
    """Exact-match half of litellm get_fallback_model_group (v1.83.10)."""
    for item in entries:
        if list(item)[0] == model_group:
            return item[model_group]
    return None


class TestLiveShape(unittest.TestCase):
    def setUp(self):
        self.text = FIXTURE.read_text()

    def test_fixture_reproduces_the_bug(self):
        rs = yaml.safe_load(self.text)["router_settings"]
        self.assertIsNone(fallback_lookup(rs["fallbacks"], "lumen/best"))
        self.assertEqual(fallback_lookup(rs["fallbacks"], "deepseek-v4-pro"), ["openrouter-deepseek-pro"])

    def test_aliases_inherit_target_chains(self):
        rs = yaml.safe_load(build(self.text)[0])["router_settings"]
        self.assertEqual(fallback_lookup(rs["fallbacks"], "lumen/best"), ["openrouter-deepseek-pro"])
        self.assertEqual(fallback_lookup(rs["fallbacks"], "lumen/fast"), ["cerebras-llama"])
        self.assertEqual(fallback_lookup(rs["context_window_fallbacks"], "lumen/fast"), ["deepseek-v4-flash"])

    def test_target_without_chain_is_a_note_not_an_addition(self):
        additions, notes = plan(yaml.safe_load(self.text))
        added = {alias for items in additions.values() for alias, _ in items}
        self.assertNotIn("lumen/vision", added)
        self.assertTrue(any(n.startswith("lumen/vision -> lumen-vision") for n in notes))

    def test_only_insertions_rest_byte_identical(self):
        new = build(self.text)[0]
        old_lines, new_lines = self.text.splitlines(), new.splitlines()
        it = iter(new_lines)
        self.assertTrue(all(any(line == n for n in it) for line in old_lines))
        self.assertEqual(len(new_lines) - len(old_lines), 6)

    def test_new_entry_lands_inside_fallbacks_not_after_pre_call_checks(self):
        lines = build(self.text)[0].splitlines()
        self.assertLess(lines.index("  - lumen/best:"), lines.index("  enable_pre_call_checks: true"))

    def test_idempotent(self):
        once = build(self.text)[0]
        self.assertEqual(build(once)[0], once)
        self.assertEqual(plan(yaml.safe_load(once))[0], {})


class TestOtherShapes(unittest.TestCase):
    def test_explicit_alias_entry_is_kept(self):
        text = ("router_settings:\n  model_group_alias:\n    a: t\n  fallbacks:\n"
                "  - t:\n    - x\n  - a:\n    - y\n")
        self.assertEqual(build(text)[0], text)

    def test_indented_list_style_and_dict_alias(self):
        text = ("router_settings:\n    model_group_alias:\n        a: {model: t, hidden: true}\n"
                "    fallbacks:\n        - t:\n              - x\n              - z\n"
                "    num_retries: 1\n")
        new = build(text)[0]
        self.assertIn("        - a:\n              - x\n              - z\n    num_retries: 1\n", new)

    def test_quotes_names_that_are_not_plain_yaml(self):
        text = "router_settings:\n  model_group_alias:\n    'a: b': t\n  fallbacks:\n  - t:\n    - x\n"
        rs = yaml.safe_load(build(text)[0])["router_settings"]
        self.assertEqual(fallback_lookup(rs["fallbacks"], "a: b"), ["x"])


class TestCli(unittest.TestCase):
    def test_check_fails_then_write_fixes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "litellm_config.yaml"
            cfg.write_text(FIXTURE.read_text())
            os.chmod(cfg, 0o600)
            inode = cfg.stat().st_ino
            with open(os.devnull, "w") as null:
                stdout, sys.stdout = sys.stdout, null
                try:
                    self.assertEqual(main(["check", str(cfg)]), 1)
                    self.assertEqual(main(["apply", str(cfg)]), 0)
                    self.assertEqual(cfg.read_text(), FIXTURE.read_text())
                    self.assertEqual(main(["apply", str(cfg), "--write"]), 0)
                    self.assertEqual(main(["check", str(cfg)]), 0)
                finally:
                    sys.stdout = stdout
            self.assertEqual(cfg.stat().st_ino, inode)
            self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
            backups = list(Path(d).glob("litellm_config.yaml.bak-wq4102-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), FIXTURE.read_text())


if __name__ == "__main__":
    unittest.main()
