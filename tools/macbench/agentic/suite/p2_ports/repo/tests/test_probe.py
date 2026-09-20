import json
import subprocess
import unittest


class ProbeTest(unittest.TestCase):
    def test_output(self):
        p = subprocess.run(["python3", "probe_ports.py", "data/ss.txt"], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout),
                         {"ports": [22, 80, 631, 5432, 8080], "loopback_only": [631, 5432]})
