"""wq#5267: wq2836 ir-triage install.sh must not demand (or re-enable) its intended-off timer.

lumen-ir-triage.timer was disabled on purpose by the 2026-10-08 subtraction
(/home/lumen/scratch/subtraction/timers-disable-list.txt:2). wq#4910 dropped its
vtimer_scheduled but left venabled/vactive, so --verify failed forever and filed an
ops-root-apply telling the reader to run install.sh as root, whose `enable --now` (and smoke
`start` of the service) would undo the subtraction. Same class as wq#5259/wq#5263.

systemctl is shimmed; the timer state comes from SHIM_STATE ("on"/"off"). The deployed-file
checks (vfile against /home/lumen/minerva/...) are host-dependent, so only timer-state MISS
lines are asserted here.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq2836-ir-triage"
INSTALL = os.path.join(OPS, BUNDLE, "install.sh")
TIMER = "lumen-ir-triage.timer"

SHIM = r"""#!/bin/sh
cmd="$1"; shift
[ "$1" = --quiet ] && shift
st="${SHIM_STATE:-off}"
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


def run_verify(state, ir_triage_on=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        dst = os.path.join(ops, BUNDLE)
        shutil.copytree(os.path.join(OPS, BUNDLE), dst,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"))
        if ir_triage_on is not None:
            path = os.path.join(dst, "install.sh")
            text = open(path).read()
            text, n = re.subn(r"^IR_TRIAGE_ON=\d$", "IR_TRIAGE_ON=%d" % ir_triage_on, text, flags=re.M)
            assert n == 1
            with open(path, "w") as fh:
                fh.write(text)
        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        with open(os.path.join(shims, "systemctl"), "w") as fh:
            fh.write(SHIM)
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""), SHIM_STATE=state)
        p = subprocess.run(["bash", os.path.join(dst, "install.sh"), "--verify"],
                           capture_output=True, text=True, timeout=120, env=env)
        return p.returncode, p.stdout + p.stderr


def timer_misses(out):
    return [l for l in out.splitlines() if l.startswith("MISS") and (TIMER + " (") in l]


class IntendedOffVerify(unittest.TestCase):
    def test_disabled_timer_raises_no_state_miss(self):
        # The live shape on Lumen: timer disabled+inactive, intended-off.
        _, out = run_verify("off")
        self.assertEqual(timer_misses(out), [], out)

    def test_flag_on_disabled_timer_still_misses(self):
        # The probe can fail: once someone re-enables it by intent, a dead timer is a MISS.
        rc, out = run_verify("off", ir_triage_on=1)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("MISS not enabled: %s (disabled)" % TIMER, out)
        self.assertIn("MISS not active: %s (inactive)" % TIMER, out)
        self.assertIn("MISS not scheduled: %s (SubState=dead)" % TIMER, out)

    def test_flag_on_live_timer_no_state_miss(self):
        _, out = run_verify("on", ir_triage_on=1)
        self.assertEqual(timer_misses(out), [], out)

    def test_shipped_intent_is_off(self):
        text = open(INSTALL).read()
        self.assertRegex(text, r"(?m)^IR_TRIAGE_ON=0$")
        self.assertIn('vunit_file "$SRC/lumen-ir-triage.timer"', text)

    def test_install_enable_and_smoke_are_gated(self):
        # Running the bundle as root must not undo the subtraction, nor fire a pass of the
        # switched-off lane as a smoke test.
        text = open(INSTALL).read()
        self.assertRegex(text, r'if \[ "\$IR_TRIAGE_ON" = 1 \]; then\n\s*echo "== enable =="\n'
                               r'\s*systemctl enable --now lumen-ir-triage\.timer')
        self.assertEqual(text.count("systemctl enable --now"), 1)
        self.assertEqual(text.count("systemctl start lumen-ir-triage.service"), 1)
        gated = text.split('if [ "$IR_TRIAGE_ON" = 1 ]; then\n  echo "== enable =="', 1)[1].split("\nelse\n", 1)[0]
        self.assertIn("systemctl start lumen-ir-triage.service", gated)

    @unittest.skipUnless(shutil.which("systemctl") and os.path.exists("/etc/systemd/system/" + TIMER),
                         "needs the live host")
    def test_live_host_has_no_timer_state_miss(self):
        p = subprocess.run(["bash", INSTALL, "--verify"], capture_output=True, text=True, timeout=120)
        out = p.stdout + p.stderr
        self.assertEqual(timer_misses(out), [], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
