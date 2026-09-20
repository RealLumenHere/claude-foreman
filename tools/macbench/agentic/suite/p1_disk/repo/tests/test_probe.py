import json
import subprocess
import unittest


class ProbeTest(unittest.TestCase):
    def test_output(self):
        p = subprocess.run(["python3", "probe_disk.py", "data/df.txt"], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), [
            {"mount": "/mnt/backup", "use_pct": 95},
            {"mount": "/", "use_pct": 90},
            {"mount": "/data", "use_pct": 80}])
