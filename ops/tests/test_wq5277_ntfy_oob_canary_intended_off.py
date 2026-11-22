"""wq#5277: wq901 install.sh must not demand (or re-enable) its intended-off canary timer.

ntfy-oob-canary.timer was disabled on purpose by the 2026-10-08 timer massacre (wq#3648,
scratch/subtraction/timers-disable-phase2.txt:50). --verify still asserted it enabled+active,
failed forever, and filed an ops-root-apply telling the reader to run install.sh as root,
whose `enable --now` + `start` would undo the subtraction.

systemctl is shimmed: timer state comes from SHIM_STATE (on/off). The config marker and the
unit-file checks are pointed at temp copies so the run is hermetic.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq901-ntfy-oob-429"
INSTALL = os.path.join(OPS, BUNDLE, "install.sh")

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


def run_verify(state, flag=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        bundle = os.path.join(ops, BUNDLE)
        shutil.copytree(os.path.join(OPS, BUNDLE), bundle,
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        etc = os.path.join(tmp, "etc")
        os.mkdir(etc)
        conf = os.path.join(tmp, "ntfy-send.conf")
        with open(conf, "w") as fh:
            fh.write('NTFY_OOB_PRIORITIES="urgent"\n')
        for u in ("ntfy-oob-canary.service", "ntfy-oob-canary.timer"):
            shutil.copy(os.path.join(bundle, u), etc)
        path = os.path.join(bundle, "install.sh")
        text = open(path).read()
        text = text.replace("/home/lumen/etc/ntfy-send.conf", conf)
        text = text.replace("/etc/systemd/system/", etc + "/")
        if flag is not None:
            text = re.sub(r"^NTFY_OOB_CANARY_ON=\d", "NTFY_OOB_CANARY_ON=%s" % flag, text, flags=re.M)
        with open(path, "w") as fh:
            fh.write(text)
        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        with open(os.path.join(shims, "systemctl"), "w") as fh:
            fh.write(SHIM)
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""), SHIM_STATE=state)
        p = subprocess.run(["bash", path, "--verify"], capture_output=True, text=True, timeout=120, env=env)
        return p.returncode, p.stdout + p.stderr


class IntendedOffVerify(unittest.TestCase):
    def test_flag_is_off(self):
        self.assertRegex(open(INSTALL).read(), r"(?m)^NTFY_OOB_CANARY_ON=0$")

    def test_disabled_timer_verifies_ok(self):
        # The live shape on Lumen: timer disabled+inactive, units + conf present.
        rc, out = run_verify("off")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ntfy-oob-canary.timer (", out)

    def test_flag_on_restores_state_checks(self):
        # The probe can fail: with the decision flipped, a disabled timer is a MISS again.
        rc, out = run_verify("off", flag=1)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("MISS not enabled: ntfy-oob-canary.timer (disabled)", out)
        self.assertIn("MISS not active: ntfy-oob-canary.timer (inactive)", out)

    def test_flag_on_and_timer_on_verifies_ok(self):
        rc, out = run_verify("on", flag=1)
        self.assertEqual(rc, 0, out)

    def test_unit_files_still_verified(self):
        text = open(INSTALL).read()
        self.assertIn('vunit_file "$SRC/ntfy-oob-canary.timer"', text)
        self.assertIn('vunit_file "$SRC/ntfy-oob-canary.service"', text)

    def test_install_gates_enable_and_start_on_flag(self):
        # Running the bundle as root must not undo the subtraction.
        text = open(INSTALL).read()
        gate = text.index('if [ "$NTFY_OOB_CANARY_ON" != 1 ]; then')
        self.assertLess(gate, text.index("systemctl enable --now ntfy-oob-canary.timer"))
        self.assertLess(gate, text.index("systemctl start ntfy-oob-canary.service"))

    @unittest.skipUnless(shutil.which("systemctl") and os.path.exists("/etc/systemd/system/ntfy-oob-canary.timer"),
                         "needs the live host")
    def test_live_host_verify_ok(self):
        p = subprocess.run(["bash", INSTALL, "--verify"], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
