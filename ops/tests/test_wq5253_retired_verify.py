"""wq#5253: wq1406 install.sh --verify must not demand intended-off timers be live.

wq1406-paid-fallback-guard is a RETIRED bundle (wq#3486). Its --verify asserted that
lumen-budget-alert.timer and lumen-deepseek-spend.timer were enabled+active, but the
2026-10-08 subtraction disabled both on purpose, so --verify failed forever and filed an
ops-root-apply that no root run could clear (install.sh refuses to install, exit 2).
The verify now checks: retired units absent, replacement unit files present.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq1406-paid-fallback-guard"
INSTALL = os.path.join(OPS, BUNDLE, "install.sh")
REPLACEMENTS = ("lumen-budget-alert.timer", "lumen-deepseek-spend.timer")
RETIRED = ("paid-spend-tripwire.service", "paid-spend-tripwire.timer")


def run(args, unit_dir=None):
    env = dict(os.environ)
    env.pop("WQ1406_UNIT_DIR", None)
    env.pop("WQ1406_FORCE_RETIRED_DEPLOY", None)
    if unit_dir is not None:
        env["WQ1406_UNIT_DIR"] = unit_dir
    p = subprocess.run(["bash", INSTALL, *args], capture_output=True, text=True, timeout=120, env=env)
    return p.returncode, p.stdout + p.stderr


def unit_dir(tmp, present):
    for u in present:
        open(os.path.join(tmp, u), "w").close()
    return tmp


class RetiredBundleVerify(unittest.TestCase):
    def test_no_enable_or_active_assertion_left(self):
        text = open(INSTALL).read()
        for u in REPLACEMENTS:
            self.assertNotRegex(text, r"(?m)^\s*(venabled|vactive|vtimer_scheduled)\s+" + u)

    def test_replacements_present_retired_absent_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run(["--verify"], unit_dir(tmp, REPLACEMENTS))
        self.assertEqual(rc, 0, out)
        self.assertIn("VERIFY OK", out)

    def test_missing_replacement_unit_file_is_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run(["--verify"], unit_dir(tmp, REPLACEMENTS[:1]))
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS replacement unit file absent: %s/%s" % (tmp, REPLACEMENTS[1]), out)

    def test_retired_unit_present_is_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run(["--verify"], unit_dir(tmp, REPLACEMENTS + RETIRED[1:]))
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS retired unit present: %s/%s" % (tmp, RETIRED[1]), out)

    def test_install_still_refuses(self):
        rc, out = run([])
        self.assertEqual(rc, 2, out)
        self.assertIn("refuse: wq#1406", out)

    @unittest.skipUnless(shutil.which("systemctl") and os.path.isdir("/etc/systemd/system"),
                         "needs the live host")
    def test_live_host_verify_passes(self):
        # Real /etc/systemd/system on Lumen: both replacement timers disabled+inactive
        # (intended-off), paid-spend-tripwire never installed.
        rc, out = run(["--verify"])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("MISS", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
