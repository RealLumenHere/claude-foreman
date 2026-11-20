#!/usr/bin/env python3
"""wq#5173: wq1613 install.sh --verify must agree with install.sh about the gated timer.

install.sh leaves lumen-state-verify.timer disabled while it is disabled (Frank gate wq#3750);
--verify used to MISS on that same state, so the bundle could never verify. Now:
  disabled + inactive          -> GATED line, no timer MISS
  disabled + active            -> MISS (gate bypassed)
  LUMEN_VERIFY_ENABLE_TIMER=1  -> enabled/active required as before
systemctl is shimmed; nothing live is queried for the timer and nothing is written.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq1613-minerva-verifier-registry"


def run_verify(active_state, extra_env=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        shutil.copytree(os.path.join(OPS, BUNDLE), os.path.join(ops, BUNDLE),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        with open(os.path.join(shims, "systemctl"), "w") as fh:
            fh.write('#!/bin/sh\ncase "$1" in\n'
                     '  is-enabled) [ "$2" = --quiet ] || echo disabled; exit 1 ;;\n'
                     f'  is-active) echo {active_state}; [ {active_state} = active ] ;;\n'
                     '  *) exit 0 ;;\nesac\n')
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""))
        env.pop("LUMEN_VERIFY_ENABLE_TIMER", None)
        env.update(extra_env or {})
        out = subprocess.run(["bash", os.path.join(ops, BUNDLE, "install.sh"), "--verify"],
                             capture_output=True, text=True, timeout=120, env=env)
        return out.stdout + out.stderr


class GatedTimerVerifyTest(unittest.TestCase):
    def test_disabled_inactive_is_gated_not_miss(self):
        out = run_verify("inactive")
        self.assertIn("GATED lumen-state-verify.timer", out)
        self.assertNotIn("MISS not enabled: lumen-state-verify.timer", out)
        self.assertNotIn("MISS not active: lumen-state-verify.timer", out)

    def test_disabled_but_active_is_miss(self):
        out = run_verify("active")
        self.assertIn("MISS lumen-state-verify.timer disabled but active", out)
        self.assertNotIn("GATED", out)

    def test_enable_override_requires_enabled_and_active(self):
        out = run_verify("inactive", {"LUMEN_VERIFY_ENABLE_TIMER": "1"})
        self.assertIn("MISS not enabled: lumen-state-verify.timer", out)
        self.assertIn("MISS not active: lumen-state-verify.timer", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
