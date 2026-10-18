#!/usr/bin/env python3
"""wq#4258 — ops/wq4258-fleet-consolidation-plan/install.sh --verify.

MUST FAIL (exit 1, one MISS line per missing model) while the live LiteLLM
config carries neither `claude-sonnet` nor `claude-haiku`, and MUST PASS
(exit 0) once both model_name markers are present. Points
WQ4258_LIVE_CONFIG at a temp fixture so the real /home/lumen/agent/
litellm_config.yaml is never touched or read.

Runs standalone: python3 ops/tests/test_wq4258_fleet_consolidation_verify.py
"""
import os
import subprocess
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
INSTALL = os.path.join(REPO, "ops", "wq4258-fleet-consolidation-plan", "install.sh")

NEITHER = "general_settings:\n  master_key: os.environ/LITELLM_MASTER_KEY\nmodel_list: []\n"
BOTH = (
    "model_list:\n"
    "  - model_name: claude-sonnet\n"
    "    litellm_params: {model: anthropic/claude-sonnet-5-5}\n"
    "  - model_name: claude-haiku\n"
    "    litellm_params: {model: anthropic/claude-haiku-5-5}\n"
)


def _verify(config_text):
    with tempfile.TemporaryDirectory(dir=os.path.join(REPO, ".tmp")) as d:
        cfg = os.path.join(d, "litellm_config.yaml")
        with open(cfg, "w") as fh:
            fh.write(config_text)
        env = dict(os.environ, WQ4258_LIVE_CONFIG=cfg)
        return subprocess.run(["bash", INSTALL, "--verify"], capture_output=True, text=True, env=env)


class VerifyContract(unittest.TestCase):
    def test_marker_precedes_any_install_step(self):
        with open(INSTALL) as fh:
            src = fh.read()
        self.assertIn('[ "${1:-}" = --verify ]', src)
        self.assertLess(src.index('[ "${1:-}" = --verify ]'), src.index("vdone"))

    def test_fails_when_neither_model_registered(self):
        r = _verify(NEITHER)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("MISS", r.stdout)
        self.assertIn("claude-sonnet", r.stdout)
        self.assertIn("claude-haiku", r.stdout)

    def test_fails_when_only_one_model_registered(self):
        r = _verify(NEITHER.replace("model_list: []", "model_list:\n  - model_name: claude-sonnet\n"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("claude-haiku", r.stdout)
        self.assertNotIn("MISS marker /model_name: claude-sonnet/", r.stdout)

    def test_passes_once_both_models_registered(self):
        r = _verify(BOTH)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFY OK", r.stdout)

    def test_default_mode_has_no_install_step(self):
        r = subprocess.run(["bash", INSTALL], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no automated install step", r.stdout)


if __name__ == "__main__":
    unittest.main()
