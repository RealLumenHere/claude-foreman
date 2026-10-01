import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from swap_recycle import is_safe_to_recycle, parse_meminfo, scan_vmswap, swap_used_kb

MEMINFO_SAMPLE = """\
MemTotal:       63649252 kB
MemFree:        20442232 kB
MemAvailable:   52087428 kB
SwapTotal:      24117244 kB
SwapFree:       12117244 kB
"""


class TestMeminfo(unittest.TestCase):
    def test_parse_meminfo(self):
        m = parse_meminfo(MEMINFO_SAMPLE)
        self.assertEqual(m["MemAvailable"], 52087428)
        self.assertEqual(m["SwapTotal"], 24117244)

    def test_swap_used_kb(self):
        m = parse_meminfo(MEMINFO_SAMPLE)
        self.assertEqual(swap_used_kb(m), 24117244 - 12117244)

    def test_safe_when_avail_exceeds_used(self):
        m = parse_meminfo(MEMINFO_SAMPLE)
        self.assertTrue(is_safe_to_recycle(m))

    def test_unsafe_when_used_exceeds_avail(self):
        m = {"MemAvailable": 1000, "SwapTotal": 50000, "SwapFree": 100}
        self.assertFalse(is_safe_to_recycle(m))

    def test_boundary_equal_is_safe(self):
        m = {"MemAvailable": 5000, "SwapTotal": 10000, "SwapFree": 5000}
        self.assertTrue(is_safe_to_recycle(m))


class TestScanVmswap(unittest.TestCase):
    def _write_status(self, root, pid, name, vmswap_kb):
        d = os.path.join(root, str(pid))
        os.makedirs(d)
        with open(os.path.join(d, "status"), "w", encoding="utf-8") as fh:
            fh.write(f"Name:\t{name}\nVmSwap:\t  {vmswap_kb} kB\n")

    def test_sorted_descending_and_skips_zero(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_status(root, 111, "alpha", 2048)
            self._write_status(root, 222, "beta", 4096)
            self._write_status(root, 333, "gamma", 0)
            results = scan_vmswap(proc_root=root)
            self.assertEqual([(kb, pid, name) for kb, pid, name in results], [
                (4096, "222", "beta"),
                (2048, "111", "alpha"),
            ])

    def test_empty_proc_root(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(scan_vmswap(proc_root=root), [])


if __name__ == "__main__":
    unittest.main()
