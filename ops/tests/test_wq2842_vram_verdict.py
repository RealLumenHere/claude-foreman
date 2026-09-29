#!/usr/bin/env python3
"""wq#2842 -- the VRAM stage must grade the RIGHT card and must not pass a bad one.

Runs standalone: python3 ops/tests/test_wq2842_vram_verdict.py
No GPU: a fake sysfs (see test_wq2842_guarded_run.Rig) plus a fake memtest_vulkan that mimics the
v0.5.0 console (menu, index on stdin, iteration lines, error lines). Marker strings are the ones in
the real v0.5.0 binary (`strings memtest_vulkan`) and the upstream Readme sample output.

Pinned:
  * REGRESSION: real memtest errors ('Error found. ... total errors 0x...') grade FAIL. The old
    grep '[1-9][0-9]*\\s*error' missed them and graded a bad card PASS.
  * card2 is tested on memtest device 2 (picked by PCI bus), card1 on device 1
  * a run that tested some other device is UNGRADED (TOOL_MISSING), never a verdict on this card
  * a card absent from the device list, an init failure, and a log with no positive pass line are
    all TOOL_MISSING, never a card FAIL / PASS
  * a runtime error after the test started (ERROR_DEVICE_LOST) is a FAIL
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_wq2842_guarded_run import BUNDLE, Base  # noqa: E402

FAKE = r'''#!/usr/bin/env bash
# fake memtest_vulkan v0.5.0: menu, then read the device index (stdin EOF => autoselect 1)
buses=(x 0x02:00 0x06:00 0x00:00)
echo "https://github.com/GpuZelenograd/memtest_vulkan v0.5.0 by GpuZelenograd"
echo "To finish testing use Ctrl+C"
echo
echo "1: Bus=0x02:00 DevId=0x6810   2GB AMD RADV CURACAO"
echo "2: Bus=0x06:00 DevId=0x6810   2GB AMD RADV CURACAO"
echo "3: Bus=0x00:00 DevId=0x0000   61GB llvmpipe (LLVM 21.1.8, 256 bits)"
idx=1
if [ -z "${FAKE_IGNORE_IDX:-}" ]; then read -r -t 2 v && idx=$v; fi
case "${FAKE_MODE:-ok}" in
  init_fail)
    echo "Runtime error: memtest_vulkan: INIT OR FIRST testing failed due to runtime error"; exit 1 ;;
esac
echo "Standard 5-minute test of $idx: Bus=${buses[$idx]} DevId=0x6810   2GB AMD RADV CURACAO"
echo "      1 iteration. Passed  1.8044 seconds  written:    0.2GB   0.6GB/sec        checked:    0.5GB   0.4GB/sec"
echo "      5 iteration. Passed  5.4135 seconds  written:    0.8GB   0.6GB/sec        checked:    1.5GB   0.4GB/sec"
case "${FAKE_MODE:-ok}" in
  ok)
    echo "Standard 5-minute test PASSed! Just press Ctrl+C unless you plan long test run."; sleep 300 ;;
  no_pass) sleep 300 ;;
  errors)
    echo "Error found. Mode NEXT_RE_READ, total errors 0x3C7EC3 out of 0x3C000000 (0.39384872%)"
    echo "Errors address range: 0x9D66148C..=0xDCD3036B  deatils:"
    sleep 300 ;;
  devlost)
    echo "Runtime error: ERROR_DEVICE_LOST while getting () in context wait_for_fences"; exit 1 ;;
esac
'''

# The upstream Readme's own bad-card output and a clean pass, as memtest_verdict.sh input.
README_BAD = """Error found. Mode NEXT_RE_READ, total errors 0x3C7EC3 out of 0x3C000000 (0.39384872%)
Errors address range: 0x9D66148C..=0xDCD3036B  deatils:
Error found. Mode INITIAL_READ, total errors 0x7E0C6E out of 0x3C000000 (0.82062860%)
Runtime error: ERROR_DEVICE_LOST while getting () in context wait_for_fences
"""
OLD_REGEX = re.compile(r"[1-9][0-9]*\s*error", re.I)


def verdict(text, bus=""):
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(text)
    try:
        p = subprocess.run(["bash", os.path.join(BUNDLE, "memtest_verdict.sh"), f.name, bus],
                           capture_output=True, text=True, timeout=20)
    finally:
        os.unlink(f.name)
    return p.returncode, p.stdout.strip()


class Verdict(unittest.TestCase):
    def test_old_regex_really_missed_the_readme_error_sample(self):
        # documents the bug: this is why the regression test below exists
        self.assertIsNone(OLD_REGEX.search(README_BAD))

    def test_readme_error_sample_is_fail(self):
        rc, out = verdict(README_BAD)
        self.assertEqual(rc, 1, out)
        self.assertTrue(out.startswith("FAIL"), out)

    def test_errors_found_banner_is_fail(self):
        rc, out = verdict("      3 iteration. Passed  5.0 seconds  written: 1GB\n"
                          "memtest_vulkan: memory/gpu ERRORS FOUND, testing finished.\n")
        self.assertEqual(rc, 1, out)

    def test_clean_pass_needs_the_positive_line(self):
        ok = ("Standard 5-minute test of 2: Bus=0x06:00 DevId=0x6810 AMD RADV\n"
              "      5 iteration. Passed  5.4 seconds  written: 1GB\n"
              "Standard 5-minute test PASSed! Just press Ctrl+C unless you plan long test run.\n")
        self.assertEqual(verdict(ok, "0x06:00")[0], 0)
        rc, out = verdict(ok.replace("Standard 5-minute test PASSed!", "still going"), "0x06:00")
        self.assertEqual(rc, 2, out)

    def test_ansi_and_cr_noise_does_not_hide_an_error(self):
        rc, out = verdict("\x1b[?25l      1 iteration. Passed 1.8 seconds\r"
                          "Error found. Mode NEXT_RE_READ, total errors 0x1 out of 0x2\r\n")
        self.assertEqual(rc, 1, out)

    def test_wrong_bus_is_ungraded_even_with_errors(self):
        log = ("Standard 5-minute test of 1: Bus=0x02:00 DevId=0x6810 AMD RADV\n"
               "      1 iteration. Passed 1.8 seconds\n"
               "Error found. Mode NEXT_RE_READ, total errors 0x1 out of 0x2\n")
        rc, out = verdict(log, "0x06:00")
        self.assertEqual(rc, 2, out)
        self.assertIn("WRONG_DEVICE", out)

    def test_missing_or_empty_log_is_ungraded(self):
        self.assertEqual(verdict("")[0], 2)
        self.assertEqual(subprocess.run(["bash", os.path.join(BUNDLE, "memtest_verdict.sh"),
                                         "/nonexistent.log"], capture_output=True).returncode, 2)


class VramStage(Base):
    def setUp(self):
        self.rig_ = self.rig({"card1": 50000, "card2": 50000}, cpu=50000)
        for card, addr in (("card1", "0000:02:00.0"), ("card2", "0000:06:00.0")):
            self.pci(card, addr)
        self.fake = os.path.join(self.rig_.root, "memtest_vulkan")
        with open(self.fake, "w") as f:
            f.write(FAKE)
        os.chmod(self.fake, 0o755)
        self.out = os.path.join(self.rig_.root, "vram-out")

    def pci(self, card, addr):
        dev = self.rig_.dev(card)
        real = os.path.join(self.rig_.root, "pci", addr)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        shutil.move(dev, real)
        os.symlink(real, dev)

    def run_stage(self, card="card2", mode="ok", **extra):
        env = self.rig_.env(GPU_CARDS=card, MEMTEST_VULKAN_BIN=self.fake, FAKE_MODE=mode,
                            VRAM_ENUM_SECONDS="1", **extra)
        p = subprocess.run(["bash", os.path.join(BUNDLE, "vram_test.sh"), self.out, "4", "90"],
                           env=env, capture_output=True, text=True, timeout=90)
        with open(os.path.join(self.out, "vram_test.json")) as f:
            res = json.load(f)
        return p, res

    def assertClampRestored(self):
        for c in ("card1", "card2"):
            self.assertEqual((self.rig_.level(c), self.rig_.state(c)), ("low", "battery"), c)

    def test_card2_is_tested_on_device_2_and_passes(self):
        p, res = self.run_stage("card2", "ok")
        self.assertEqual(res["result"], "PASS", (res, p.stderr))
        self.assertIn("device 2 at 0x06:00", res["detail"])
        self.assertClampRestored()

    def test_card1_is_tested_on_device_1(self):
        p, res = self.run_stage("card1", "ok")
        self.assertEqual(res["result"], "PASS", (res, p.stderr))
        self.assertIn("device 1 at 0x02:00", res["detail"])

    def test_real_memory_errors_grade_fail(self):
        p, res = self.run_stage("card2", "errors")
        self.assertEqual(res["result"], "FAIL", (res, p.stderr))
        self.assertIn("Error found", res["detail"])
        self.assertClampRestored()

    def test_device_lost_after_start_is_fail(self):
        p, res = self.run_stage("card2", "devlost")
        self.assertEqual(res["result"], "FAIL", (res, p.stderr))

    def test_wrong_device_is_tool_missing_not_a_verdict(self):
        # tool ignores the index and tests device 1 while card2 is under test
        p, res = self.run_stage("card2", "ok", FAKE_IGNORE_IDX="1")
        self.assertEqual(res["result"], "TOOL_MISSING", (res, p.stderr))
        self.assertIn("WRONG_DEVICE", res["detail"])

    def test_card_absent_from_device_list_is_tool_missing(self):
        os.unlink(self.rig_.dev("card2"))
        real = os.path.join(self.rig_.root, "pci", "0000:0a:00.0")
        shutil.move(os.path.join(self.rig_.root, "pci", "0000:06:00.0"), real)
        os.symlink(real, self.rig_.dev("card2"))
        p, res = self.run_stage("card2", "ok")
        self.assertEqual(res["result"], "TOOL_MISSING", (res, p.stderr))
        self.assertIn("not in the memtest_vulkan device list", res["detail"])

    def test_init_failure_is_tool_missing_not_card_fail(self):
        p, res = self.run_stage("card2", "init_fail")
        self.assertEqual(res["result"], "TOOL_MISSING", (res, p.stderr))

    def test_no_positive_pass_line_is_not_a_pass(self):
        p, res = self.run_stage("card2", "no_pass")
        self.assertEqual(res["result"], "TOOL_MISSING", (res, p.stderr))
        self.assertClampRestored()


if __name__ == "__main__":
    unittest.main(verbosity=2)
