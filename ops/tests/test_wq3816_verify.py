#!/usr/bin/env python3
"""wq#3816 — wq3816-healer-drop-desktop-commander-mcp/install.sh --verify must fail while the
masked desktop-commander-mcp row is in the healer watch table, and pass once it is gone.

Runs standalone: python3 ops/tests/test_wq3816_verify.py
Read-only: every case points HEALER_WATCH_TABLE at a temp file; the live table is never read.
"""
import os
import subprocess
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
INSTALL = os.path.join(REPO, "ops", "wq3816-healer-drop-desktop-commander-mcp", "install.sh")

CLEAN = (
    "units:\n"
    "  mcp-gateway:           {kind: systemd, policy: restart}\n"
    "  # wq#3816: desktop-commander-mcp row removed, unit masked.\n"
    "  ntfy:                  {kind: docker,  policy: restart}\n"
)
WITH_ROW = CLEAN + "  desktop-commander-mcp: {kind: systemd, policy: restart}\n"


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
        self.assertIn("desktop-commander-mcp", CLEAN)
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


if __name__ == "__main__":
    unittest.main()
