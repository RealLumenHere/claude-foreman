#!/usr/bin/env python3
"""wq#5271: wq3249 install.sh --verify treats dc-handles-scrub.timer as intended-ON.

The 2026-10-08 subtraction disabled the timer with an explicit re-enable condition
("if DC-on-Lumen is used again", scratch/subtraction/credential-consolidation.md:27).
Commander runs on Lumen again (wq#4921) and the handle store held credential-shaped
rows, so wq#5271 re-enabled it and --verify now also asserts a real future elapse
(vtimer_scheduled), not just enabled+active.

systemctl is shimmed so the test does not depend on live state:
  * on   -> enabled, active, SubState=waiting, finite NextElapse: no timer MISS
  * off  -> disabled, inactive: MISS on enabled/active/scheduled (the probe can fail)
  * stuck-> enabled+active but SubState=elapsed, NextElapse=infinity: only the
            vtimer_scheduled line catches it (proves the new line does work)
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq3249-dc-handles-scrub"
UNIT = "dc-handles-scrub.timer"

SHIMS = {
    "on": ('  is-enabled) [ "$2" = --quiet ] || echo enabled; exit 0 ;;\n'
           '  is-active) echo active; exit 0 ;;\n'
           '  show) case "$4" in SubState) echo waiting ;; NextElapseUSecMonotonic) echo 1d ;;'
           ' *) echo infinity ;; esac; exit 0 ;;\n'),
    "off": ('  is-enabled) [ "$2" = --quiet ] || echo disabled; exit 1 ;;\n'
            '  is-active) echo inactive; exit 3 ;;\n'
            '  show) case "$4" in SubState) echo dead ;; *) echo infinity ;; esac; exit 0 ;;\n'),
    "stuck": ('  is-enabled) [ "$2" = --quiet ] || echo enabled; exit 0 ;;\n'
              '  is-active) echo active; exit 0 ;;\n'
              '  show) case "$4" in SubState) echo elapsed ;; *) echo infinity ;; esac; exit 0 ;;\n'),
}


def run_verify(mode, bundle_text=None):
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
            fh.write('#!/bin/sh\ncase "$1" in\n' + SHIMS[mode] + '  *) exit 0 ;;\nesac\n')
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""))
        out = subprocess.run(["bash", script, "--verify"],
                             capture_output=True, text=True, timeout=120, env=env)
        return out.stdout + out.stderr


def timer_misses(out):
    return [ln for ln in out.splitlines() if ln.startswith("MISS") and UNIT in ln]


class IntendedOnTimerVerifyTest(unittest.TestCase):
    def test_enabled_scheduled_timer_raises_no_timer_miss(self):
        self.assertEqual(timer_misses(run_verify("on")), [])

    def test_disabled_timer_is_flagged(self):
        out = run_verify("off")
        self.assertIn(f"not enabled: {UNIT}", out)
        self.assertIn(f"not active: {UNIT}", out)
        self.assertIn(f"not scheduled: {UNIT}", out)

    def test_elapsed_timer_caught_only_by_vtimer_scheduled(self):
        out = run_verify("stuck")
        self.assertEqual(timer_misses(out), [f"MISS not scheduled: {UNIT} (SubState=elapsed)"])
        text = open(os.path.join(OPS, BUNDLE, "install.sh")).read()
        old = text.replace(f"  vtimer_scheduled {UNIT}\n", "", 1)
        self.assertNotEqual(old, text)
        self.assertEqual(timer_misses(run_verify("stuck", old)), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
