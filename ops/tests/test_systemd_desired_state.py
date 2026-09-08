#!/usr/bin/env python3
"""wq#1531 — staged-but-not-installed detection for systemd_desired_state.
Runs standalone: python3 ops/tests/test_systemd_desired_state.py
Nothing live is touched: SDS_FOREMAN and SYS are both redirected into tmp, and
_enabled is stubbed, so no systemctl call and no /etc/systemd/system read happen."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir))
import systemd_desired_state as sds


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


class StagedDriftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.foreman = os.path.join(self.tmp, "foreman")
        self.sysdir = os.path.join(self.tmp, "etc")
        os.makedirs(self.sysdir, exist_ok=True)
        self._saved = (sds.FOREMAN, sds.SYS, sds._enabled)
        sds.FOREMAN, sds.SYS = self.foreman, self.sysdir
        sds._enabled = lambda unit: "enabled"

    def tearDown(self):
        sds.FOREMAN, sds.SYS, sds._enabled = self._saved

    def stage(self, relpath, text="[Unit]\nDescription=x\n"):
        return _write(os.path.join(self.foreman, relpath), text)

    def install(self, name, text="[Unit]\nDescription=x\n"):
        return _write(os.path.join(self.sysdir, name), text)

    # ---- staged_paths scoping --------------------------------------------------
    def test_only_lumen_units_under_a_systemd_dir_are_staged(self):
        self.stage("harness/systemd/lumen-a.timer")
        self.stage("dispatcher/systemd/lumen-b.service")
        self.stage("harness/systemd/wq-worker@.service")   # template: instantiated
        self.stage("harness/systemd/vendor-thing.service")  # not lumen-*: vendor
        self.stage("harness/lumen-loose.timer")             # not under */systemd/
        self.assertEqual([b for b, _ in sds.staged_paths()],
                         ["lumen-a.timer", "lumen-b.service"])

    # ---- the wq#1531 case ------------------------------------------------------
    def test_staged_never_installed_is_missing(self):
        self.stage("harness/systemd/lumen-read-dir-sweep.timer")
        self.stage("harness/systemd/lumen-read-dir-sweep.service")
        d = sds.staged_drift()
        self.assertEqual(d["missing"],
                         ["lumen-read-dir-sweep.service", "lumen-read-dir-sweep.timer"])
        self.assertEqual((d["stale"], d["disabled"]), ([], []))

    def test_installed_and_identical_is_clean(self):
        body = "[Unit]\nDescription=same\n"
        self.stage("harness/systemd/lumen-a.timer", body)
        self.install("lumen-a.timer", body)
        self.assertEqual(sds.staged_drift(),
                         {"missing": [], "stale": [], "disabled": []})
        self.assertEqual(sds.staged_report()[0], 0)

    def test_installed_with_different_content_is_stale(self):
        self.stage("harness/systemd/lumen-a.timer", "OnCalendar=05:15\n")
        self.install("lumen-a.timer", "OnCalendar=23:00\n")
        self.assertEqual(sds.staged_drift()["stale"], ["lumen-a.timer"])

    # ---- enablement ------------------------------------------------------------
    def test_installed_but_not_enabled_timer_is_disabled(self):
        body = "[Unit]\n"
        self.stage("harness/systemd/lumen-a.timer", body)
        self.install("lumen-a.timer", body)
        sds._enabled = lambda unit: "disabled"
        self.assertEqual(sds.staged_drift()["disabled"], ["lumen-a.timer"])

    def test_unknown_unit_file_state_is_not_reported_disabled(self):
        """systemctl unavailable -> "" ; an unknown state is not evidence of one."""
        body = "[Unit]\n"
        self.stage("harness/systemd/lumen-a.timer", body)
        self.install("lumen-a.timer", body)
        sds._enabled = lambda unit: ""
        self.assertEqual(sds.staged_drift()["disabled"], [])

    def test_service_enablement_is_not_checked(self):
        """A .service pulled by its .timer is legitimately `static`/disabled."""
        body = "[Unit]\n"
        self.stage("harness/systemd/lumen-a.service", body)
        self.install("lumen-a.service", body)
        sds._enabled = lambda unit: "disabled"
        self.assertEqual(sds.staged_drift()["disabled"], [])

    # ---- report / exit codes ---------------------------------------------------
    def test_staged_command_reports_and_exits_nonzero(self):
        self.stage("harness/systemd/lumen-a.timer")
        n, lines = sds.staged_report()
        self.assertEqual(n, 1)
        self.assertIn("MISSING", lines[0])
        self.assertIn("lumen-a.timer", lines[0])
        self.assertEqual(sds.staged(), 1)

    def test_staged_command_clean_exits_zero(self):
        self.assertEqual(sds.staged(), 0)


if __name__ == "__main__":
    unittest.main()
