#!/usr/bin/env python3
"""wq#5252: wq1396 install.sh --verify must not demand that minerva-promote.timer run.

The 2026-10-08 subtraction (scratch/subtraction/REPORT.md 1A) disabled the whole Minerva
proposal machine, minerva-promote.timer included, on purpose. --verify asserted
venabled+vactive on it, so it failed forever and filed an ops-root-apply telling the reader
to run the bundle as root. Now --verify checks the wq1500 successor's unit files are
installed and the superseded wq1396 units/binary are absent; it does not check run state.
systemctl is shimmed to report the timer disabled+inactive; nothing is written.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq1396-minerva-proposal-promoter"
SUCCESSOR = "wq1500-minerva-promoter"
INSTALL = os.path.join(OPS, BUNDLE, "install.sh")


def verify_block():
    text = open(INSTALL).read()
    m = re.search(r'if \[ "\$\{1:-\}" = --verify \]; then\n(.*?)\n  vdone\nfi', text, re.S)
    assert m, "no --verify block"
    return m.group(1)


def run(args, shim_disabled=True, extra_env=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        for b in (BUNDLE, SUCCESSOR):
            shutil.copytree(os.path.join(OPS, b), os.path.join(ops, b),
                            ignore=shutil.ignore_patterns("__pycache__"))
        env = dict(os.environ)
        env.pop("WQ1396_FORCE_LEGACY", None)
        if shim_disabled:
            shims = os.path.join(tmp, "shims")
            os.mkdir(shims)
            with open(os.path.join(shims, "systemctl"), "w") as fh:
                fh.write('#!/bin/sh\ncase "$1" in\n'
                         '  is-enabled) echo disabled; exit 1 ;;\n'
                         '  is-active) echo inactive; exit 3 ;;\n'
                         '  show) echo dead; exit 0 ;;\n'
                         '  *) exit 0 ;;\nesac\n')
            os.chmod(os.path.join(shims, "systemctl"), 0o755)
            env["PATH"] = shims + os.pathsep + env.get("PATH", "")
        env.update(extra_env or {})
        return subprocess.run(["bash", os.path.join(ops, BUNDLE, "install.sh")] + args,
                              capture_output=True, text=True, env=env, timeout=60)


class Wq1396Verify(unittest.TestCase):
    def test_no_run_state_assertion_on_intended_off_timer(self):
        block = verify_block()
        for fn in ("venabled", "vactive", "vtimer_scheduled"):
            self.assertNotRegex(block, re.compile(rf"^\s*{fn}\s+minerva-promote\.timer", re.M))

    def test_successor_unit_files_still_checked(self):
        block = verify_block()
        for unit in ("minerva-promote.service", "minerva-promote.timer"):
            self.assertIn(f'"$HERE/../{SUCCESSOR}/{unit}"', block)
            self.assertIn(f"/etc/systemd/system/{unit}", block)
            self.assertTrue(os.path.isfile(os.path.join(OPS, SUCCESSOR, unit)))

    def test_superseded_checks_kept(self):
        block = verify_block()
        self.assertIn("superseded unit present", block)
        self.assertIn("superseded binary present", block)

    @unittest.skipUnless(os.path.exists("/etc/systemd/system/minerva-promote.timer"),
                         "wq1500 units not installed on this host")
    def test_disabled_inactive_timer_verifies_ok(self):
        r = run(["--verify"])
        self.assertNotIn("MISS not enabled: minerva-promote.timer", r.stdout)
        self.assertNotIn("MISS not active: minerva-promote.timer", r.stdout)
        self.assertNotIn("minerva-promote", "".join(l for l in r.stdout.splitlines(True)
                                                    if l.startswith("MISS")))

    def test_install_still_refuses_without_force(self):
        r = run([], shim_disabled=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("superseded", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
