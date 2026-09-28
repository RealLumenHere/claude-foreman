#!/usr/bin/env python3
"""wq#2842 -- the GPU grading harness must hold the rig safe while it stresses it.

Runs standalone: python3 ops/tests/test_wq2842_guarded_run.py
No GPU or rig needed: a fake DRM + hwmon tree stands in for sysfs (DRM_ROOT / HWMON_ROOT).

Pinned behaviour (design: reports/2842_d300_harness_design.md):
  * the low-power clamp is lifted (level AND state) only while the load runs, and put back
  * a hot card or hot CPU package kills the load group LIVE (exit 97), not after the soak
  * abort armed with no readable GPU temp fails closed
  * a clamp that cannot be lifted returns 98 and the load never starts
  * SIGTERM to the wrapper still restores the clamp and reaps the load
  * a hold/restore failure is TOOL_MISSING (rig unfit), never a card FAIL
"""
import csv
import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, os.pardir, os.pardir)
BUNDLE = os.path.abspath(os.path.join(REPO, "ops", "wq2714-d300-gpu-resale-validation"))
LVL = "power_dpm_force_performance_level"
ST = "power_dpm_state"


def rd(path):
    with open(path) as f:
        return f.read().strip()


class Rig:
    """A fake sysfs. temps: {card: millidegrees or None for 'no hwmon'}."""

    def __init__(self, temps, cpu=None):
        self.root = tempfile.mkdtemp(prefix="wq2842-")
        self.drm = os.path.join(self.root, "drm")
        self.hwmon = os.path.join(self.root, "hwmon")
        os.makedirs(self.hwmon)
        for card, temp in temps.items():
            dev = os.path.join(self.drm, card, "device")
            os.makedirs(dev)
            self.put(os.path.join(dev, LVL), "low")
            self.put(os.path.join(dev, ST), "battery")
            self.put(os.path.join(dev, "gpu_busy_percent"), "0")
            self.put(os.path.join(dev, "pp_dpm_sclk"), "0: 300Mhz *\n1: 1000Mhz\n")
            if temp is not None:
                hw = os.path.join(dev, "hwmon", "hwmon0")
                os.makedirs(hw)
                self.put(os.path.join(hw, "temp1_input"), str(temp))
        if cpu is not None:
            hw = os.path.join(self.hwmon, "hwmon9")
            os.makedirs(hw)
            self.put(os.path.join(hw, "name"), "coretemp")
            self.put(os.path.join(hw, "temp1_input"), str(cpu))

    @staticmethod
    def put(path, text):
        with open(path, "w") as f:
            f.write(text + "\n")

    def dev(self, card):
        return os.path.join(self.drm, card, "device")

    def level(self, card):
        return rd(os.path.join(self.dev(card), LVL))

    def state(self, card):
        return rd(os.path.join(self.dev(card), ST))

    def env(self, **extra):
        env = dict(os.environ)
        env.update({
            "DRM_ROOT": self.drm, "HWMON_ROOT": self.hwmon,
            "GPU_HOLD_SUDO": "/bin/false", "GPU_LOWPOWER_SH": "/nonexistent",
            "SAMPLE_INTERVAL_S": "1",
        })
        env.pop("GPU_CARDS", None)
        env.update(extra)
        return env


class Base(unittest.TestCase):
    def rig(self, temps, cpu=None):
        r = Rig(temps, cpu)
        self.addCleanup(shutil.rmtree, r.root, True)
        return r

    def guard(self, rig, cmd, max_temp="90", duration=None, env=None, timeout=60):
        out = os.path.join(rig.root, "out")
        args = ["bash", os.path.join(BUNDLE, "guarded_run.sh"), "--outdir", out,
                "--tag", "t", "--max-temp-c", max_temp]
        if duration is not None:
            args += ["--duration", str(duration)]
        args += ["--"] + cmd
        t0 = time.time()
        proc = subprocess.run(args, env=env or rig.env(), capture_output=True, text=True,
                              timeout=timeout)
        return proc, out, time.time() - t0

    def summary(self, out):
        d = {}
        with open(os.path.join(out, "t.guard")) as f:
            for line in f:
                k, _, v = line.rstrip("\n").partition("=")
                d[k] = v
        return d


class GuardedRun(Base):
    def test_clamp_lifted_only_while_running_and_restored(self):
        rig = self.rig({"card1": 50000})
        seen = os.path.join(rig.root, "seen")
        cmd = ["bash", "-c", 'cat "%s" "%s" > "%s"; sleep 2' % (
            os.path.join(rig.dev("card1"), LVL), os.path.join(rig.dev("card1"), ST), seen)]
        proc, out, _ = self.guard(rig, cmd)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(rd(seen).split(), ["auto", "performance"])
        self.assertEqual((rig.level("card1"), rig.state("card1")), ("low", "battery"))
        self.assertEqual(self.summary(out)["restore"], "ok")

    def test_load_exit_code_passes_through(self):
        rig = self.rig({"card1": 50000})
        proc, _, _ = self.guard(rig, ["bash", "-c", "sleep 1; exit 3"])
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(rig.level("card1"), "low")

    def test_deadline_returns_124_and_restores(self):
        rig = self.rig({"card1": 50000})
        proc, _, took = self.guard(rig, ["sleep", "60"], duration=3)
        self.assertEqual(proc.returncode, 124, proc.stderr)
        self.assertLess(took, 25)
        self.assertEqual(rig.level("card1"), "low")

    def test_hot_card_aborts_live_and_restores(self):
        rig = self.rig({"card1": 99000})
        proc, out, took = self.guard(rig, ["sleep", "60"], max_temp="90")
        self.assertEqual(proc.returncode, 97, proc.stderr)
        self.assertLess(took, 30, "abort must not wait for the load to finish")
        self.assertIn("card1", rd(os.path.join(out, ".t_thermal_abort")))
        self.assertEqual((rig.level("card1"), rig.state("card1")), ("low", "battery"))

    def test_second_card_is_watched_too(self):
        rig = self.rig({"card1": 50000, "card2": 99000})
        proc, out, _ = self.guard(rig, ["sleep", "60"], max_temp="90")
        self.assertEqual(proc.returncode, 97, proc.stderr)
        self.assertIn("card2", rd(os.path.join(out, ".t_thermal_abort")))
        with open(os.path.join(out, "t_telemetry.csv")) as f:
            cards = {r["card"] for r in csv.DictReader(f)}
        self.assertEqual(cards, {"card1", "card2"})

    def test_hot_cpu_package_aborts(self):
        rig = self.rig({"card1": 50000}, cpu=99000)
        proc, out, _ = self.guard(rig, ["sleep", "60"], env=rig.env(CPU_ABORT_C="90"))
        self.assertEqual(proc.returncode, 97, proc.stderr)
        self.assertIn("cpu package", rd(os.path.join(out, ".t_thermal_abort")))
        self.assertEqual(rig.level("card1"), "low")

    def test_gpu_cards_holds_only_the_card_under_test(self):
        rig = self.rig({"card1": 50000, "card2": 50000})
        seen = os.path.join(rig.root, "seen")
        cmd = ["bash", "-c", 'cat "%s" "%s" > "%s"; sleep 2' % (
            os.path.join(rig.dev("card1"), LVL), os.path.join(rig.dev("card2"), LVL), seen)]
        proc, _, _ = self.guard(rig, cmd, env=rig.env(GPU_CARDS="card2"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(rd(seen).split(), ["low", "auto"])
        self.assertEqual((rig.level("card1"), rig.level("card2")), ("low", "low"))

    def test_abort_armed_without_gpu_temp_fails_closed(self):
        rig = self.rig({"card1": None})
        proc, out, _ = self.guard(rig, ["sleep", "60"])
        self.assertEqual(proc.returncode, 97, proc.stderr)
        self.assertIn("no_temp_telemetry", rd(os.path.join(out, ".t_thermal_abort")))
        self.assertEqual(rig.level("card1"), "low")

    def test_clamp_that_cannot_be_lifted_returns_98_and_load_never_starts(self):
        rig = self.rig({"card1": 50000})
        os.chmod(os.path.join(rig.dev("card1"), LVL), stat.S_IRUSR | stat.S_IRGRP)
        marker = os.path.join(rig.root, "started")
        proc, _, _ = self.guard(rig, ["bash", "-c", 'touch "%s"' % marker])
        self.assertEqual(proc.returncode, 98, proc.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_clamp_that_cannot_be_restored_returns_99(self):
        rig = self.rig({"card1": 50000})
        knob = os.path.join(rig.dev("card1"), LVL)
        # the load pins the knob read-only after the hold engaged, so put-back cannot write
        proc, out, _ = self.guard(rig, ["bash", "-c", 'chmod 444 "%s"; sleep 1' % knob])
        self.assertEqual(proc.returncode, 99, proc.stderr)
        self.assertIn("GPU_RESTORE_FAILED", proc.stderr)
        self.assertEqual(self.summary(out)["restore"], "FAILED")
        os.chmod(knob, 0o644)

    def test_sigterm_to_wrapper_restores_and_reaps_load(self):
        rig = self.rig({"card1": 50000})
        out = os.path.join(rig.root, "out")
        pidfile = os.path.join(rig.root, "load.pid")
        cmd = ["bash", "-c", 'echo $$ > "%s"; exec sleep 120' % pidfile]
        p = subprocess.Popen(
            ["bash", os.path.join(BUNDLE, "guarded_run.sh"), "--outdir", out, "--tag", "t",
             "--max-temp-c", "90", "--"] + cmd,
            env=rig.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)
        for _ in range(150):
            if os.path.exists(pidfile) and rig.level("card1") == "auto":
                break
            time.sleep(0.1)
        self.assertEqual(rig.level("card1"), "auto", "hold never engaged")
        load_pid = int(rd(pidfile))
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)
        self.assertEqual((rig.level("card1"), rig.state("card1")), ("low", "battery"))
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(load_pid, 0)


class StageMapping(Base):
    """compute_soak.sh maps guard outcomes onto PASS / FAIL / TOOL_MISSING."""

    def stage(self, rig, clpeak_body="sleep 1", max_temp="90"):
        stubs = os.path.join(rig.root, "stubs")
        os.makedirs(stubs, exist_ok=True)
        path = os.path.join(stubs, "clpeak")
        with open(path, "w") as f:
            f.write("#!/bin/sh\n%s\n" % clpeak_body)
        os.chmod(path, 0o755)
        out = os.path.join(rig.root, "stage")
        env = rig.env(PATH=stubs + os.pathsep + os.environ["PATH"])
        proc = subprocess.run(
            ["bash", os.path.join(BUNDLE, "compute_soak.sh"), out, "3", max_temp],
            env=env, capture_output=True, text=True, timeout=120)
        with open(os.path.join(out, "compute_soak.json")) as f:
            return proc, json.load(f)

    @unittest.skipIf(shutil.which("gpu-burn"), "gpu-burn present, stub clpeak would not be used")
    def test_cool_card_passes(self):
        rig = self.rig({"card1": 50000})
        proc, res = self.stage(rig)
        self.assertEqual(res["result"], "PASS", res)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(rig.level("card1"), "low")

    @unittest.skipIf(shutil.which("gpu-burn"), "gpu-burn present, stub clpeak would not be used")
    def test_hot_card_fails_with_thermal_abort(self):
        rig = self.rig({"card1": 99000})
        proc, res = self.stage(rig, clpeak_body="sleep 30")
        self.assertEqual(res["result"], "FAIL", res)
        self.assertIn("thermal abort", res["detail"])
        self.assertEqual(rig.level("card1"), "low")

    @unittest.skipIf(shutil.which("gpu-burn"), "gpu-burn present, stub clpeak would not be used")
    def test_unliftable_clamp_is_tool_missing_not_a_card_fail(self):
        rig = self.rig({"card1": 50000})
        os.chmod(os.path.join(rig.dev("card1"), LVL), stat.S_IRUSR | stat.S_IRGRP)
        proc, res = self.stage(rig)
        self.assertEqual(res["result"], "TOOL_MISSING", res)
        self.assertIn("GPU_HOLD_FAILED", res["detail"])


class OrchestratorGrade(unittest.TestCase):
    STUB = """#!/usr/bin/env bash
source "$(dirname "$0")/lib_common.sh"
write_result "$1/{stage}.json" "{stage}" "{result}" "stub"
[ "{result}" = PASS ]
"""

    def run_with(self, results, extra=()):
        tmp = tempfile.mkdtemp(prefix="wq2842-orch-")
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in ("orchestrator.sh", "lib_common.sh"):
            shutil.copy(os.path.join(BUNDLE, name), tmp)
        for stage in ("bind_check", "vram_test", "compute_soak"):
            p = os.path.join(tmp, stage + ".sh")
            with open(p, "w") as f:
                f.write(self.STUB.format(stage=stage, result=results.get(stage, "PASS")))
            os.chmod(p, 0o755)
        export = os.path.join(tmp, "export")
        env = dict(os.environ, GRADE_EXPORT_DIR=export)
        proc = subprocess.run(
            ["bash", os.path.join(tmp, "orchestrator.sh"), "--serial", "S1",
             "--outdir", os.path.join(tmp, "out")] + list(extra),
            env=env, capture_output=True, text=True, timeout=60)
        return proc, tmp, export

    def test_pass_emits_headless_grade_but_not_resale_ready(self):
        proc, tmp, export = self.run_with({}, extra=["--card", "card1"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        files = os.listdir(export)
        self.assertEqual(len(files), 1)
        with open(os.path.join(export, files[0])) as f:
            g = json.load(f)
        self.assertEqual((g["serial"], g["headless_grade"], g["card"]), ("S1", "PASS", "card1"))
        self.assertFalse(g["resale_ready"])
        self.assertEqual(g["visual_gate"], "pending")
        self.assertEqual(g["stages"]["compute_soak"]["result"], "PASS")

    def test_tool_missing_is_exported_as_tool_missing(self):
        proc, tmp, export = self.run_with({"vram_test": "TOOL_MISSING"})
        self.assertEqual(proc.returncode, 1)
        with open(os.path.join(export, os.listdir(export)[0])) as f:
            g = json.load(f)
        self.assertEqual((g["headless_grade"], g["failed_stage"]), ("TOOL_MISSING", "vram_test"))
        self.assertNotIn("compute_soak", g["stages"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
