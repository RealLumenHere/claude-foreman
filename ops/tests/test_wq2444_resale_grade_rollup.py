#!/usr/bin/env python3
"""wq#2444 — the resale grade log must carry each stage's own result.

Runs standalone: python3 ops/tests/test_wq2444_resale_grade_rollup.py
No GPU or rig needed: the real orchestrator.sh + lib_common.sh are copied into a temp
dir next to stub stage scripts that write a chosen result to <stage>.json.

The defect being pinned (wq#2716): orchestrator.sh branched on each stage's exit code
and wrote the literal `FAIL` into grades/resale_grades.csv. Stages exit 1 for both FAIL
and TOOL_MISSING, so a rig missing `vulkaninfo` logged a scrap-grade FAIL against a card
that was never tested -- and the SOP tells the operator to read that CSV as the verdict.
"""
import csv
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
BUNDLE = os.path.join(REPO, "ops", "wq2714-d300-gpu-resale-validation")
STAGES = ("bind_check", "vram_test", "compute_soak")

STUB = """#!/usr/bin/env bash
source "$(dirname "$0")/lib_common.sh"
result="{result}"
if [ "$result" != NOJSON ]; then
  write_result "$1/{stage}.json" "{stage}" "$result" "stub"
fi
[ "$result" = PASS ]
"""


class GradeRollup(unittest.TestCase):
    def run_with(self, results):
        tmp = tempfile.mkdtemp(prefix="wq2444-")
        self.addCleanup(shutil.rmtree, tmp)
        for name in ("orchestrator.sh", "lib_common.sh"):
            shutil.copy(os.path.join(BUNDLE, name), tmp)
        for stage in STAGES:
            path = os.path.join(tmp, stage + ".sh")
            with open(path, "w") as f:
                f.write(STUB.format(stage=stage, result=results.get(stage, "PASS")))
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        proc = subprocess.run(
            ["bash", os.path.join(tmp, "orchestrator.sh"), "--serial", "T1",
             "--outdir", os.path.join(tmp, "out")],
            capture_output=True, text=True, timeout=60)
        with open(os.path.join(tmp, "grades", "resale_grades.csv")) as f:
            rows = list(csv.DictReader(f))
        return proc, rows

    def test_tool_missing_is_not_logged_as_fail(self):
        proc, rows = self.run_with({"bind_check": "TOOL_MISSING"})
        self.assertEqual(proc.returncode, 1)
        self.assertEqual([(r["stage"], r["result"]) for r in rows],
                         [("bind_check", "TOOL_MISSING")])
        self.assertIn("HEADLESS_GRADE=TOOL_MISSING stage=bind_check", proc.stdout)

    def test_tool_missing_mid_chain(self):
        _, rows = self.run_with({"compute_soak": "TOOL_MISSING"})
        self.assertEqual([(r["stage"], r["result"]) for r in rows],
                         [("compute_soak", "TOOL_MISSING")])

    def test_real_fail_stays_fail_and_stops_chain(self):
        _, rows = self.run_with({"vram_test": "FAIL"})
        self.assertEqual([(r["stage"], r["result"]) for r in rows],
                         [("vram_test", "FAIL")])

    def test_stage_crash_without_json_is_fail(self):
        _, rows = self.run_with({"bind_check": "NOJSON"})
        self.assertEqual([(r["stage"], r["result"]) for r in rows],
                         [("bind_check", "FAIL")])

    def test_all_pass(self):
        proc, rows = self.run_with({})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual([(r["stage"], r["result"]) for r in rows],
                         [("compute_soak", "PASS")])


if __name__ == "__main__":
    unittest.main()
