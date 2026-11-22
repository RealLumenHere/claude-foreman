#!/usr/bin/env python3
"""wq#5260: wq2152 install.sh --verify must not demand the intended-off timer be on.

lumen-chunk-doc-invariants.timer was disabled on purpose by the 2026-10-08 subtraction
(/home/lumen/scratch/subtraction/timers-disable-list.txt:28). wq#4910 dropped only the
vtimer_scheduled check, so --verify still asserted venabled/vactive, failed forever, and
filed an ops-root-apply telling the reader to run install.sh as root, whose
`systemctl enable --now` would undo the subtraction.

systemctl is shimmed to report the timer disabled + inactive (the live, intended state);
the run must raise no timer MISS. The unit-file checks still compare against
/etc/systemd/system, so only timer-state lines are asserted here.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq2152-pgvector-invariants-timer"
SRC_BUNDLE = "wq1776-pgvector-chunk-doc-invariants"
UNIT = "lumen-chunk-doc-invariants.timer"


def run_verify():
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        for b in (BUNDLE, SRC_BUNDLE):
            shutil.copytree(os.path.join(OPS, b), os.path.join(ops, b),
                            ignore=shutil.ignore_patterns("__pycache__"))
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
        out = subprocess.run(["bash", os.path.join(ops, BUNDLE, "install.sh"), "--verify"],
                             capture_output=True, text=True, timeout=120, env=env)
        return out.stdout + out.stderr


class IntendedOffTimerVerifyTest(unittest.TestCase):
    def test_disabled_inactive_timer_raises_no_miss(self):
        out = run_verify()
        self.assertNotIn(f"not enabled: {UNIT}", out)
        self.assertNotIn(f"not active: {UNIT}", out)
        self.assertNotRegex(out, re.escape(UNIT) + r".*(elapsed|not scheduled|no future)")

    def test_verify_block_has_no_timer_state_asserts(self):
        text = open(os.path.join(OPS, BUNDLE, "install.sh")).read()
        block = text.split('= --verify ]; then', 1)[1].split("\nfi\n", 1)[0]
        for fn in ("venabled", "vactive", "vtimer_scheduled"):
            self.assertIsNone(re.search(r"^\s*" + fn + r"\s+" + re.escape(UNIT), block, re.M), fn)
        self.assertIn("timers-disable-list.txt:28", block)
        self.assertRegex(block, r"vunit_file\s+\"\$HERE/" + re.escape(UNIT) + '"')

    def test_install_does_not_enable_or_start_timer(self):
        text = open(os.path.join(OPS, BUNDLE, "install.sh")).read()
        body = text.split("\nfi\n", 1)[1]
        self.assertIsNone(re.search(r"^[^#\n]*systemctl\s+(enable|start)\b", body, re.M))


if __name__ == "__main__":
    unittest.main(verbosity=2)
