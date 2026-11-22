#!/usr/bin/env python3
"""wq#5272: wq3335 install.sh --verify must not demand the intended-off timer be on.

cf-changelog-watch.timer was disabled on purpose by the 2026-10-08 subtraction
(/home/lumen/scratch/subtraction/timers-disable-phase2.txt:2). --verify used to assert
venabled/vactive on it, so it failed forever and filed an ops-root-apply telling the
reader to run install.sh as root (`systemctl enable --now`), which would undo the
subtraction.

systemctl is shimmed to report the timer disabled + inactive (the live, intended state);
the run must raise no timer MISS. A second run re-inserting the old asserts under the
same shim must flag them (the probe can fail). The file/unit-file checks still compare
against the live paths, so only timer-state lines are asserted here.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq3335-cf-changelog-watch"
UNIT = "cf-changelog-watch.timer"


def run_verify(bundle_text=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        shutil.copytree(os.path.join(OPS, BUNDLE), os.path.join(ops, BUNDLE),
                        ignore=shutil.ignore_patterns("__pycache__"))
        script = os.path.join(ops, BUNDLE, "install.sh")
        if bundle_text is not None:
            with open(script, "w") as fh:
                fh.write(bundle_text)
        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        with open(os.path.join(shims, "systemctl"), "w") as fh:
            fh.write('#!/bin/sh\ncase "$1" in\n'
                     '  is-enabled) [ "$2" = --quiet ] || echo disabled; exit 1 ;;\n'
                     '  is-active) echo inactive; exit 3 ;;\n'
                     '  show) echo; exit 0 ;;\n'
                     '  *) exit 0 ;;\nesac\n')
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""))
        out = subprocess.run(["bash", script, "--verify"],
                             capture_output=True, text=True, timeout=120, env=env)
        return out.stdout + out.stderr


class IntendedOffTimerVerifyTest(unittest.TestCase):
    def test_disabled_inactive_timer_raises_no_miss(self):
        out = run_verify()
        self.assertNotIn(f"not enabled: {UNIT}", out)
        self.assertNotIn(f"not active: {UNIT}", out)
        self.assertNotRegex(out, re.escape(UNIT) + r".*(elapsed|not scheduled|no future)")

    def test_old_asserts_would_have_failed_under_same_shim(self):
        # Falsifiability: re-insert the pre-wq#5272 asserts; the same shim must flag them.
        text = open(os.path.join(OPS, BUNDLE, "install.sh")).read()
        old = text.replace("  vdone\n", f"  vactive {UNIT}\n  venabled {UNIT}\n  vdone\n", 1)
        self.assertNotEqual(old, text)
        out = run_verify(old)
        self.assertIn(f"not enabled: {UNIT}", out)
        self.assertIn(f"not active: {UNIT}", out)

    def test_verify_block_has_no_timer_state_asserts(self):
        text = open(os.path.join(OPS, BUNDLE, "install.sh")).read()
        block = text.split('= --verify ]; then', 1)[1].split("\nfi\n", 1)[0]
        for fn in ("venabled", "vactive", "vtimer_scheduled"):
            self.assertIsNone(re.search(r"^\s*" + fn + r"\s+" + re.escape(UNIT), block, re.M), fn)
        self.assertIn("timers-disable-phase2.txt:2", block)
        self.assertRegex(block, r"vunit_file\s+\"\$SRC/" + re.escape(UNIT) + '"')


if __name__ == "__main__":
    unittest.main(verbosity=2)
