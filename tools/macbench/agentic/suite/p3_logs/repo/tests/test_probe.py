import subprocess
import unittest


class ProbeTest(unittest.TestCase):
    def test_output(self):
        p = subprocess.run(["python3", "probe_logs.py", "data/app.log"], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.splitlines(),
                         ["2026-09-01T13 2", "2026-09-01T15 3", "2026-09-02T00 1"])
