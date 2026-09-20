import subprocess
import unittest


def probe(env_file):
    return subprocess.run(["python3", "probe_env.py", "data/required.txt", env_file],
                          capture_output=True, text=True, timeout=30)


class ProbeTest(unittest.TestCase):
    def test_missing(self):
        p = probe("data/env.json")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stdout.splitlines(), ["API_KEY", "SECRET_TOKEN"])

    def test_ok(self):
        p = probe("data/env_ok.json")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "ok")
