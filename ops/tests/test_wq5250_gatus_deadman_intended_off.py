"""wq#5250: ops/wq1184-gatus-health-checks must treat lumen-cron-deadman.timer as
intended-off (disabled by the 2026-10-08 subtraction, timers-disable-list.txt:78).

--verify must not assert it enabled/active (that failed permanently and filed an
ops-root-apply telling the reader to run install.sh as root), and the install body must
not `systemctl enable --now` it unless LUMEN_CRON_DEADMAN_ENABLE=1 is set.
"""
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
INSTALL = os.path.join(REPO, "ops", "wq1184-gatus-health-checks", "install.sh")
UNIT = "lumen-cron-deadman.timer"


def _split(text):
    start = text.index('if [ "${1:-}" = --verify ]; then')
    end = text.index("\nfi\n", start)
    return text[start:end], text[end:]


def _code(block):
    return "\n".join(l for l in block.splitlines() if not l.lstrip().startswith("#"))


class DeadmanIntendedOff(unittest.TestCase):
    def setUp(self):
        with open(INSTALL) as f:
            self.verify, self.body = _split(f.read())

    def test_verify_does_not_assert_deadman_on(self):
        code = _code(self.verify)
        for fn in ("venabled", "vactive", "vtimer_scheduled"):
            self.assertIsNone(re.search(r"^\s*" + fn + r"\s+" + re.escape(UNIT), code, re.M), fn)

    def test_verify_still_checks_deadman_unit_files_and_relay(self):
        code = _code(self.verify)
        self.assertIn("/etc/systemd/system/lumen-cron-deadman.timer", code)
        self.assertIn("/etc/systemd/system/lumen-cron-deadman.service", code)
        self.assertRegex(code, r"(?m)^\s*venabled\s+lumen-gatus-ntfy-relay\.service")

    def test_verify_cites_the_subtraction_record(self):
        self.assertIn("timers-disable-list.txt:78", self.verify)

    def test_install_enables_deadman_only_on_opt_in(self):
        code = _code(self.body)
        lines = [l for l in code.splitlines() if re.search(r"systemctl\s+enable.*" + re.escape(UNIT), l)]
        self.assertEqual(len(lines), 1, lines)
        guard = code[: code.index(lines[0])].rstrip().splitlines()[-1]
        self.assertIn('"${LUMEN_CRON_DEADMAN_ENABLE:-0}" = 1', guard)

    def test_subtraction_record_still_lists_it(self):
        rec = "/home/lumen/scratch/subtraction/timers-disable-list.txt"
        if not os.path.exists(rec):
            self.skipTest("subtraction record not readable here")
        with open(rec) as f:
            self.assertIn(UNIT.rsplit(".", 1)[0], f.read().split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
