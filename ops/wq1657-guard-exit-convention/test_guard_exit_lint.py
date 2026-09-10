#!/usr/bin/env python3
"""Tests for guard_exit_lint (wq#1657). Run: python3 test_guard_exit_lint.py"""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "staged"))
import guard_exit_lint as gel

MODE_B_OK = """[Unit]
Description=pat scan
OnFailure=ntfy-onfailure@%n.service
[Service]
X-Lumen-Guard=finding-exit
ExecStart=/home/lumen/bin/pat-scan
"""

TAG_NO_ONFAILURE = """[Unit]
Description=drifty
[Service]
X-Lumen-Guard=finding-exit
ExecStart=/bin/true
"""

PLAIN = """[Unit]
Description=ordinary daemon
[Service]
ExecStart=/bin/true
"""


class LintUnit(unittest.TestCase):
    def test_mode_b_clean(self):
        self.assertEqual(gel.lint_unit("lumen-pat-scan.service", MODE_B_OK), [])

    def test_gel1_tag_without_onfailure(self):
        v = gel.lint_unit("whatever.service", TAG_NO_ONFAILURE)
        self.assertEqual(len(v), 1)
        self.assertIn("GEL1", v[0])

    def test_gel2_known_guard_untagged(self):
        v = gel.lint_unit("wq-drift-guard.service", PLAIN)
        self.assertEqual(len(v), 1)
        self.assertIn("GEL2", v[0])
        self.assertIn("wq-drift-guard.service", v[0])

    def test_all_ten_seeded(self):
        self.assertEqual(len(gel.KNOWN_FINDING_EXIT), 10)
        for u in gel.KNOWN_FINDING_EXIT:
            self.assertTrue(any("GEL2" in x for x in gel.lint_unit(u, PLAIN)), u)

    def test_plain_unit_clean(self):
        self.assertEqual(gel.lint_unit("nginx.service", PLAIN), [])

    def test_tag_is_exact_value(self):
        text = PLAIN + "X-Lumen-Guard=something-else\n"
        self.assertEqual(gel.lint_unit("nginx.service", text), [])


class LintDir(unittest.TestCase):
    def _tree(self, files):
        d = tempfile.mkdtemp()
        for rel, content in files.items():
            path = os.path.join(d, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
        return d

    def test_dropin_tag_and_onfailure_count(self):
        # tag in the unit, OnFailure supplied by a drop-in -> clean
        d = self._tree({
            "lumen-budget-alert.service": TAG_NO_ONFAILURE,
            "lumen-budget-alert.service.d/50-guard.conf":
                "[Unit]\nOnFailure=ntfy-onfailure@%n.service\n",
        })
        self.assertEqual(gel.lint_dir(d), [])

    def test_dropin_alone_tags_known_guard(self):
        d = self._tree({
            "unit-drift.service": PLAIN + "OnFailure=ntfy-onfailure@%n.service\n",
            "unit-drift.service.d/50-guard.conf":
                "[Service]\nX-Lumen-Guard=finding-exit\n",
        })
        self.assertEqual(gel.lint_dir(d), [])

    def test_dir_reports_violations_and_skips_non_services(self):
        d = self._tree({
            "wq-drift-guard.service": PLAIN,       # GEL2
            "tagged.service": TAG_NO_ONFAILURE,    # GEL1
            "some.timer": TAG_NO_ONFAILURE,        # ignored: not a .service
        })
        v = gel.lint_dir(d)
        self.assertEqual(len(v), 2)
        self.assertTrue(any("GEL1 tagged.service" in x for x in v))
        self.assertTrue(any("GEL2 wq-drift-guard.service" in x for x in v))

    def test_unreadable_file_fails_open(self):
        d = self._tree({"wq-drift-guard.service": PLAIN})
        boom = lambda p: (_ for _ in ()).throw(IOError("nope"))
        v = gel.lint_dir(d, read=boom)
        self.assertEqual(len(v), 1)          # unreadable text -> only GEL2
        self.assertIn("GEL2", v[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
