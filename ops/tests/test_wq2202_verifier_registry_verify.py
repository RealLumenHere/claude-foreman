#!/usr/bin/env python3
"""wq#2202 — the wq1613-minerva-verifier-registry bundle answers `install.sh --verify`.

Runs standalone: python3 ops/tests/test_wq2202_verifier_registry_verify.py
The handler is read-only against the box, so the run is safe anywhere; what it
reports depends on whether the bundle is deployed, so the cases pin the contract
(marker before any install step, exit code in {0,1}, MISS lines name real targets,
nothing installed) rather than a fixed verdict.
"""
import os
import re
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
INSTALL = os.path.join(REPO, "ops", "wq1613-minerva-verifier-registry", "install.sh")

TARGETS = (
    "/home/lumen/bin/lumen-verify",
    "/home/lumen/minerva/verify/registry.yaml",
    "lumen-state-verify.service",
    "lumen-state-verify.timer",
)


def _verify():
    return subprocess.run(["bash", INSTALL, "--verify"], capture_output=True, text=True)


class VerifyContract(unittest.TestCase):
    def test_marker_precedes_every_install_step(self):
        with open(INSTALL) as fh:
            src = fh.read()
        marker = src.index('[ "${1:-}" = --verify ]')
        for step in ("pytest", "install -m", "sudo ", "repair_clobbered_facts.py"):
            self.assertLess(marker, src.index(step), step)

    def test_verdict_and_exit_code_agree(self):
        r = _verify()
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)
        if r.returncode == 0:
            self.assertIn("VERIFY OK", r.stdout)
        else:
            self.assertRegex(r.stdout, r"VERIFY FAIL \(\d+\)")

    def test_every_miss_names_a_bundle_target(self):
        r = _verify()
        for line in r.stdout.splitlines():
            if line.startswith("MISS "):
                self.assertTrue(any(t in line for t in TARGETS), line)

    def test_verify_installs_nothing(self):
        r = _verify()
        self.assertNotRegex(r.stdout + r.stderr, re.compile(r"\d+ passed|Created symlink"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
