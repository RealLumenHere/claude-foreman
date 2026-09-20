#!/usr/bin/env python3
"""wq#2284 — the wq1528-minerva-factpack-budget bundle answers `install.sh --verify`.

Runs standalone: python3 ops/tests/test_wq2284_factpack_budget_verify.py
The handler is read-only against the box; what it reports depends on whether the bundle is
deployed, so the cases pin the contract (marker before any install step, exit code in {0,1},
MISS lines name real targets, nothing installed) rather than a fixed verdict.
"""
import os
import re
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
INSTALL = os.path.join(REPO, "ops", "wq1528-minerva-factpack-budget", "install.sh")
BACKUP_DIR = "/home/lumen/backups/wq1528"

TARGETS = (
    "/home/lumen/minerva/minerva_factpack.py",
    "/home/lumen/minerva/minerva_verify.py",
    "/etc/systemd/system/minerva-proposal.service.d/zz-wq1528-judge-window.conf",
)


def _verify():
    return subprocess.run(["bash", INSTALL, "--verify"], capture_output=True, text=True)


def _backups():
    return sorted(os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else None


class VerifyContract(unittest.TestCase):
    def test_marker_precedes_every_install_step(self):
        with open(INSTALL) as fh:
            src = fh.read()
        marker = src.index('[ "${1:-}" = --verify ]')
        for step in ("mkdir -p", "install -m", "cp -p", "systemctl daemon-reload", "declare -A PRE"):
            self.assertLess(marker, src.index(step), step)

    def test_verdict_and_exit_code_agree(self):
        r = _verify()
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)
        miss = [ln for ln in r.stdout.splitlines() if ln.startswith("MISS ")]
        self.assertEqual(r.returncode == 1, bool(miss), r.stdout)

    def test_miss_lines_name_real_targets(self):
        for ln in _verify().stdout.splitlines():
            if ln.startswith("MISS "):
                self.assertTrue(any(t in ln or os.path.basename(t) in ln for t in TARGETS), ln)

    def test_read_only(self):
        before = _backups()
        r = _verify()
        self.assertNotRegex(r.stdout, re.compile(r"^installed:", re.M))
        self.assertEqual(_backups(), before)


if __name__ == "__main__":
    unittest.main()
