#!/usr/bin/env python3
"""wq#4103 tests for judge_local_primary.py.

Run: python3 ops/wq4103-judge-local-primary/tests/test_judge_local_primary.py
"""
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "judge_local_primary.py"
FIXTURE = HERE / "fixtures" / "live_judge_config.yaml"

import yaml

spec = importlib.util.spec_from_file_location("judge_local_primary", TOOL)
jlp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jlp)


def _tmp_copy(src: Path) -> Path:
    d = Path(tempfile.mkdtemp())
    dst = d / src.name
    shutil.copy2(src, dst)
    return dst


class ClassifyTest(unittest.TestCase):
    def test_fixture_is_hathor_primary(self):
        cfg = yaml.safe_load(FIXTURE.read_text())
        self.assertEqual(jlp.classify(cfg), "hathor-primary")

    def test_check_exit_code_on_hathor_primary(self):
        rc = jlp.main(["check", str(FIXTURE)])
        self.assertEqual(rc, 1)  # not yet applied


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.path = _tmp_copy(FIXTURE)

    def test_apply_write_flips_to_local_primary(self):
        rc = jlp.main(["apply", str(self.path), "--write"])
        self.assertEqual(rc, 0)
        cfg = yaml.safe_load(self.path.read_text())
        self.assertEqual(jlp.classify(cfg), "local-primary")
        self.assertEqual(jlp.main(["check", str(self.path)]), 0)

    def test_transform_delta_is_exact(self):
        before = FIXTURE.read_text()
        before_cfg = yaml.safe_load(before)
        jlp.main(["apply", str(self.path), "--write"])
        after_cfg = yaml.safe_load(self.path.read_text())

        # alias added, model_name renamed, fallback removed -- nothing else.
        self.assertEqual(after_cfg["router_settings"]["model_group_alias"]["lumen/judge"],
                         "lumen-local-judge")
        names = [m["model_name"] for m in after_cfg["model_list"]]
        self.assertNotIn("lumen/judge", names)
        self.assertIn("hathor-judge", names)
        self.assertIn("lumen-local-judge", names)
        self.assertFalse(any("lumen/judge" in f for f in after_cfg["router_settings"]["fallbacks"]))

        # the renamed rung keeps every litellm_param (timeout 10 etc) intact --
        # only the model_name string changed.
        old_judge = next(m for m in before_cfg["model_list"] if m["model_name"] == "lumen/judge")
        new_rung = next(m for m in after_cfg["model_list"] if m["model_name"] == "hathor-judge")
        self.assertEqual(new_rung["litellm_params"], old_judge["litellm_params"])
        self.assertEqual(new_rung.get("model_info"), old_judge.get("model_info"))

    def test_line_diff_is_minimal(self):
        before = FIXTURE.read_text().split("\n")
        jlp.main(["apply", str(self.path), "--write"])
        after = self.path.read_text().split("\n")
        # +1 alias line, -2 fallback lines => net -1 line; exactly one model_name edit.
        self.assertEqual(len(after), len(before) - 1)
        self.assertIn("    lumen/judge: lumen-local-judge", after)
        self.assertNotIn("  - lumen/judge:", after)

    def test_idempotent(self):
        jlp.main(["apply", str(self.path), "--write"])
        rc = jlp.main(["apply", str(self.path), "--write"])
        self.assertEqual(rc, 0)  # already local-primary, no-op


class GuardTest(unittest.TestCase):
    """Falsifiable probe drawn from OUTSIDE the happy-path fixture: a config whose
    lumen/judge fallback holds an unexpected extra item must make the editor
    refuse, not silently mangle (wq#2630 -- the probe must be able to fail)."""

    def test_refuses_fallback_with_extra_item(self):
        text = FIXTURE.read_text().replace(
            "  - lumen/judge:\n    - lumen-local-judge\n",
            "  - lumen/judge:\n    - lumen-local-judge\n    - some-other-rung\n")
        d = Path(tempfile.mkdtemp())
        p = d / "mangled.yaml"
        p.write_text(text)
        with self.assertRaises(SystemExit):
            jlp.main(["apply", str(p), "--write"])
        # the file must be untouched (write only happens after validation)
        self.assertEqual(p.read_text(), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
