"""wq#5257: wq1704 install.sh must not demand (or re-enable) its intended-off timers.

lumen-spof-roundtrip.timer and lumen-spof-restarts.timer were disabled on purpose by the
2026-10-08 subtraction (scratch/subtraction/timers-disable-list.txt:71-72). --verify still
asserted all four timers enabled+active, failed forever, and filed an ops-root-apply telling
the reader to run install.sh as root, whose `enable --now` loop would undo the subtraction.

systemctl is shimmed per unit: the per-unit state comes from SHIM_<check> env vars.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq1704-missing-watchdogs"
INSTALL = os.path.join(OPS, BUNDLE, "install.sh")
ON = ("daemons", "timers")
OFF = ("roundtrip", "restarts")

# State per check: "on" -> enabled/active/waiting with a finite elapse; "off" -> disabled/
# inactive/dead. Picks the check out of the unit name lumen-spof-<check>.timer.
SHIM = r"""#!/bin/sh
cmd="$1"; shift
[ "$1" = --quiet ] && shift
u="$1"; c="${u#lumen-spof-}"; c="${c%.timer}"
eval st="\${SHIM_$c:-off}"
case "$cmd" in
  is-active)  [ "$st" = on ] && echo active  || echo inactive; exit 0 ;;
  is-enabled) [ "$st" = on ] && echo enabled || echo disabled; exit 0 ;;
  show)
    case "$3" in
      SubState) [ "$st" = on ] && echo waiting || echo dead ;;
      NextElapseUSecMonotonic) [ "$st" = on ] && echo 5min || echo infinity ;;
      NextElapseUSecRealtime) echo infinity ;;
    esac
    exit 0 ;;
  *) exit 0 ;;
esac
"""


def run_verify(states):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        shutil.copytree(os.path.join(OPS, BUNDLE), os.path.join(ops, BUNDLE),
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        with open(os.path.join(shims, "systemctl"), "w") as fh:
            fh.write(SHIM)
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""))
        env.update({"SHIM_" + c: s for c, s in states.items()})
        p = subprocess.run(["bash", os.path.join(ops, BUNDLE, "install.sh"), "--verify"],
                           capture_output=True, text=True, timeout=120, env=env)
        return p.returncode, p.stdout + p.stderr


def timer_misses(out):
    return [l for l in out.splitlines() if l.startswith("MISS") and ".timer (" in l
            or l.startswith("MISS no finite")]


class IntendedOffVerify(unittest.TestCase):
    def test_off_timers_disabled_raise_no_state_miss(self):
        # The live shape on Lumen: daemons/timers on, roundtrip/restarts disabled+inactive.
        _, out = run_verify({"daemons": "on", "timers": "on", "roundtrip": "off", "restarts": "off"})
        self.assertEqual(timer_misses(out), [], out)
        for c in OFF:
            self.assertNotIn("lumen-spof-%s.timer (" % c, out)

    def test_on_timer_disabled_still_misses(self):
        # The probe can fail: an intended-on timer going off must still be a MISS.
        rc, out = run_verify({"daemons": "off", "timers": "on", "roundtrip": "off", "restarts": "off"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("MISS not enabled: lumen-spof-daemons.timer (disabled)", out)
        self.assertIn("MISS not active: lumen-spof-daemons.timer (inactive)", out)
        self.assertIn("MISS not scheduled: lumen-spof-daemons.timer (SubState=dead)", out)
        self.assertNotIn("lumen-spof-timers.timer (", out)

    def test_off_unit_files_still_verified(self):
        text = open(INSTALL).read()
        self.assertRegex(text, r'for c in "\$\{CHECKS\[@\]\}"; do\n\s*vunit_file "\$SRC/lumen-spof-\$c\.service"')

    def test_enabled_set_is_exactly_the_intended_on_pair(self):
        text = open(INSTALL).read()
        m = re.search(r"^ENABLED=\(([^)]*)\)", text, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(tuple(m.group(1).split()), ON)

    def test_install_never_enables_or_starts_over_all_checks(self):
        # Running the bundle as root must not undo the subtraction.
        text = open(INSTALL).read()
        for verb in ("enable --now", "start"):
            loop = re.search(r'for c in "\$\{(\w+)\[@\]\}"; do\n\s*systemctl ' + verb, text)
            self.assertIsNotNone(loop, verb)
            self.assertEqual(loop.group(1), "ENABLED", verb)
        self.assertNotIn("journalctl -u lumen-spof-roundtrip", text)

    @unittest.skipUnless(shutil.which("systemctl") and os.path.exists("/etc/systemd/system/lumen-spof-roundtrip.timer"),
                         "needs the live host")
    def test_live_host_has_no_timer_state_miss(self):
        # Real systemd on Lumen: roundtrip/restarts disabled+inactive (intended-off).
        p = subprocess.run(["bash", INSTALL, "--verify"], capture_output=True, text=True, timeout=120)
        out = p.stdout + p.stderr
        self.assertEqual(timer_misses(out), [], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
