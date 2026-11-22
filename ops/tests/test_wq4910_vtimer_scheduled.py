#!/usr/bin/env python3
"""wq#4910: vtimer_scheduled is rolled into every intended-on timer bundle's --verify.

wq#4733 added ops/_verify.sh vtimer_scheduled because `vactive` (systemctl is-active) also
passes a timer in SubState=elapsed that will never fire again. Its negative test so far only
covered the not-waiting branch; this file pins all three branches of the helper, the shape a
bundle reaches it through, and the per-bundle decision this ticket made:

  intended-on timer  -> `vtimer_scheduled <unit>` follows its `vactive <unit>`
  intended-off timer -> a `# wq#4910: no vtimer_scheduled for <unit>: intended-off` note
                        naming its 2026-10-08 subtraction record instead, so --verify does
                        not grow a second reason to re-enable a timer disabled on purpose.

systemctl is shimmed; nothing live is queried or written.
"""
import glob
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")

SHIM = """#!/bin/sh
case "$1" in
  is-active) echo active; exit 0 ;;
  is-enabled) [ "$2" = --quiet ] || echo enabled; exit 0 ;;
  show)
    case "$4" in
      SubState) echo "$SHIM_SUB" ;;
      NextElapseUSecMonotonic) echo "$SHIM_MONO" ;;
      NextElapseUSecRealtime) echo "$SHIM_REAL" ;;
    esac
    exit 0 ;;
  *) exit 0 ;;
esac
"""


def _env(tmp, sub, mono, real):
    shims = os.path.join(tmp, "shims")
    os.makedirs(shims, exist_ok=True)
    path = os.path.join(shims, "systemctl")
    with open(path, "w") as fh:
        fh.write(SHIM)
    os.chmod(path, 0o755)
    return dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""),
                SHIM_SUB=sub, SHIM_MONO=mono, SHIM_REAL=real)


def run_helper(sub, mono, real, unit="x.timer"):
    """Source _verify.sh, call vtimer_scheduled alone, then vdone."""
    with tempfile.TemporaryDirectory() as tmp:
        script = f'. "{OPS}/_verify.sh"; vtimer_scheduled {unit}; vdone'
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             timeout=60, env=_env(tmp, sub, mono, real))
        return out.returncode, out.stdout + out.stderr


def run_bundle(bundle, sub, mono, real):
    """Run a copied bundle's install.sh --verify against the shim (read-only path)."""
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        shutil.copytree(os.path.join(OPS, bundle), os.path.join(ops, bundle),
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        out = subprocess.run(["bash", os.path.join(ops, bundle, "install.sh"), "--verify"],
                             capture_output=True, text=True, timeout=120,
                             env=_env(tmp, sub, mono, real))
        return out.returncode, out.stdout + out.stderr


class HelperBranches(unittest.TestCase):
    def test_waiting_with_finite_monotonic_passes(self):
        rc, out = run_helper("waiting", "1w 2d 6h 45min", "")
        self.assertEqual(rc, 0, out)
        self.assertIn("VERIFY OK", out)

    def test_waiting_with_finite_realtime_passes(self):
        rc, out = run_helper("waiting", "infinity", "Mon 2026-11-23 00:00:00 UTC")
        self.assertEqual(rc, 0, out)

    def test_elapsed_fixture_misses(self):
        # The wq#4732 shape: is-active says active, SubState=elapsed, nothing scheduled.
        rc, out = run_helper("elapsed", "infinity", "")
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS not scheduled: x.timer (SubState=elapsed)", out)

    def test_elapsed_misses_even_with_a_finite_stamp(self):
        # SubState is checked first: a stale finite value must not rescue an elapsed timer.
        rc, out = run_helper("elapsed", "1h", "Mon 2026-11-23 00:00:00 UTC")
        self.assertEqual(rc, 1, out)
        self.assertIn("SubState=elapsed", out)

    def test_waiting_but_infinite_misses(self):
        # The branch wq#4733's negative test never reached.
        rc, out = run_helper("waiting", "infinity", "infinity")
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS no finite NextElapseUSec: x.timer", out)

    def test_waiting_but_empty_misses(self):
        rc, out = run_helper("waiting", "", "")
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS no finite NextElapseUSec: x.timer", out)

    def test_unknown_substate_misses(self):
        rc, out = run_helper("", "", "")
        self.assertEqual(rc, 1, out)
        self.assertIn("SubState=unknown", out)


class BundleReachesHelper(unittest.TestCase):
    """A rolled-in bundle's --verify now MISSes on elapsed where vactive alone passed."""

    BUNDLE = "wq4599-secret-backup-sweep-timer"
    UNIT = "secret-backup-sweep.timer"

    def test_elapsed_timer_misses_through_bundle(self):
        rc, out = run_bundle(self.BUNDLE, "elapsed", "infinity", "")
        self.assertNotEqual(rc, 0, out)
        self.assertIn(f"MISS not scheduled: {self.UNIT} (SubState=elapsed)", out)
        self.assertNotIn(f"MISS not active: {self.UNIT}", out)  # vactive alone was fooled

    def test_waiting_infinite_misses_through_bundle(self):
        rc, out = run_bundle(self.BUNDLE, "waiting", "infinity", "infinity")
        self.assertNotEqual(rc, 0, out)
        self.assertIn(f"MISS no finite NextElapseUSec: {self.UNIT}", out)

    def test_scheduled_timer_raises_no_timer_miss(self):
        _, out = run_bundle(self.BUNDLE, "waiting", "1d", "")
        self.assertNotIn(self.UNIT, "\n".join(l for l in out.splitlines() if l.startswith("MISS")))

    def test_templated_bundle_checks_only_intended_on_units(self):
        # wq1704 gates lumen-spof-$c.timer in a loop; only daemons/timers are intended-on.
        _, out = run_bundle("wq1704-missing-watchdogs", "elapsed", "infinity", "")
        self.assertIn("MISS not scheduled: lumen-spof-daemons.timer", out)
        self.assertIn("MISS not scheduled: lumen-spof-timers.timer", out)
        self.assertNotIn("not scheduled: lumen-spof-roundtrip.timer", out)
        self.assertNotIn("not scheduled: lumen-spof-restarts.timer", out)

    def test_gated_bundle_checks_schedule_when_enabled(self):
        # wq1613 is enabled here (shim), so its enabled branch must reach the helper.
        _, out = run_bundle("wq1613-minerva-verifier-registry", "elapsed", "infinity", "")
        self.assertIn("MISS not scheduled: lumen-state-verify.timer (SubState=elapsed)", out)


VACTIVE = re.compile(r'^\s*vactive\s+"?([^\s"]+\.timer)"?\s*$', re.M)
OFF_NOTE = "# wq#4910: no vtimer_scheduled for {unit}: intended-off"


class EveryTimerBundleDecided(unittest.TestCase):
    def test_every_vactive_timer_is_scheduled_or_noted_off(self):
        undecided, checked = [], 0
        for path in sorted(glob.glob(os.path.join(OPS, "*", "install.sh"))):
            text = open(path).read()
            for unit in VACTIVE.findall(text):
                checked += 1
                if "$" in unit:  # templated: the call must exist (wq1704's loop case)
                    if not re.search(r"vtimer_scheduled\s+\"?" + re.escape(unit), text):
                        undecided.append(f"{path}: {unit}")
                    continue
                scheduled = re.search(r"^\s*vtimer_scheduled\s+" + re.escape(unit) + r"\b", text, re.M)
                noted = OFF_NOTE.format(unit=unit) in text
                if not (scheduled or noted) or (scheduled and noted):
                    undecided.append(f"{os.path.relpath(path, REPO)}: {unit}")
        self.assertGreater(checked, 40)
        self.assertEqual(undecided, [], "timer bundles with no intended-on/off decision")

    def test_off_notes_cite_a_subtraction_record(self):
        for path in sorted(glob.glob(os.path.join(OPS, "*", "install.sh"))):
            text = open(path).read()
            for m in re.finditer(r"# wq#4910: no vtimer_scheduled for (\S+): intended-off, disabled by the\n"
                                 r"\s*# 2026-10-08 subtraction \(scratch/subtraction/([^)]+)\)", text):
                self.assertTrue(m.group(2).startswith(("timers-disable", "REPORT.md")), m.group(0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
