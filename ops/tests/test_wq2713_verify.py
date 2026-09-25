#!/usr/bin/env python3
"""wq#2713 — wq2713-healer-drop-redis-row/install.sh --verify must fail while the
not-found redis-server row is in the healer watch table, and pass once it is gone.

Runs standalone: python3 ops/tests/test_wq2713_verify.py
Read-only: every case points HEALER_WATCH_TABLE at a temp file; the live table is never read.
"""
import os
import subprocess
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
INSTALL = os.path.join(REPO, "ops", "wq2713-healer-drop-redis-row", "install.sh")

CLEAN = (
    "units:\n"
    "  # wq#2713: redis-server row removed, LoadState=not-found.\n"
    "  lumen-messenger:       {kind: systemd, policy: restart}\n"
    "  ntfy:                  {kind: docker,  policy: restart}\n"
)
WITH_ROW = (
    "units:\n"
    "  redis-server:          {kind: systemd, policy: restart}\n"
    "  lumen-messenger:       {kind: systemd, policy: restart}\n"
    "  ntfy:                  {kind: docker,  policy: restart}\n"
)


def _verify(table_text):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "healer_watch_table.yaml")
        if table_text is not None:
            with open(path, "w") as fh:
                fh.write(table_text)
        env = dict(os.environ, HEALER_WATCH_TABLE=path)
        return subprocess.run(["bash", INSTALL, "--verify"], env=env,
                              capture_output=True, text=True)


class VerifyContract(unittest.TestCase):
    def test_row_absent_passes(self):
        r = _verify(CLEAN)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFY OK", r.stdout)

    def test_comment_mentioning_unit_is_not_a_row(self):
        self.assertIn("redis-server", CLEAN)
        self.assertEqual(_verify(CLEAN).returncode, 0)

    def test_row_present_fails(self):
        r = _verify(WITH_ROW)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("MISS row still present", r.stdout)

    def test_unparseable_table_fails(self):
        r = _verify("units: [unclosed\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("does not parse", r.stdout)

    def test_missing_table_is_error_not_drift(self):
        r = _verify(None)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_verify_does_not_modify_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.yaml")
            with open(path, "w") as fh:
                fh.write(WITH_ROW)
            subprocess.run(["bash", INSTALL, "--verify"],
                           env=dict(os.environ, HEALER_WATCH_TABLE=path),
                           capture_output=True, text=True)
            with open(path) as fh:
                self.assertEqual(fh.read(), WITH_ROW)
            self.assertEqual(os.listdir(tmp), ["t.yaml"])

    def test_install_removes_row_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.yaml")
            with open(path, "w") as fh:
                fh.write(WITH_ROW)
            env = dict(os.environ, HEALER_WATCH_TABLE=path)
            r = subprocess.run(["bash", INSTALL], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(path) as fh:
                self.assertNotIn("redis-server", fh.read())
            self.assertTrue(os.path.exists(path + ".pre-wq2713"))
            with open(path + ".pre-wq2713") as fh:
                self.assertEqual(fh.read(), WITH_ROW)
            # idempotent: second run is a no-op, does not error
            r2 = subprocess.run(["bash", INSTALL], env=env, capture_output=True, text=True)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)


if __name__ == "__main__":
    unittest.main()
