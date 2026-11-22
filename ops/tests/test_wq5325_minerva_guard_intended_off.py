"""wq#5325: wq3147 minerva-guard install.sh must not re-enable (or demand) its intended-off timer.

lumen-minerva-guard.timer was disabled on purpose by the 2026-10-08 subtraction
(/home/lumen/scratch/subtraction/timers-disable-list.txt:4). wq#5269 dropped the --verify
timer-state checks, but install still ran `systemctl enable --now` unconditionally, so any
minerva_guard.py change -> vfile MISS -> ops-root-apply -> root install re-enabled the timer.
Same pattern as wq#5266/wq#5268: an in-file INTENDED_ON flag gates both.

systemctl is shimmed; the timer state comes from SHIM_STATE ("on"/"off"). The deployed-file
checks (vfile against /home/lumen/services, /etc/...) are host-dependent, so only timer-state
MISS lines are asserted.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq3147-minerva-guard"
INSTALL = os.path.join(OPS, BUNDLE, "install.sh")
TIMER = "lumen-minerva-guard.timer"

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


def run_verify(state, intended_on=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        dst = os.path.join(ops, BUNDLE)
        shutil.copytree(os.path.join(OPS, BUNDLE), dst,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"))
        if intended_on is not None:
            path = os.path.join(dst, "install.sh")
            text = open(path).read()
            text, n = re.subn(r"^INTENDED_ON=\d$", "INTENDED_ON=%d" % intended_on, text, flags=re.M)
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
        _, out = run_verify("off")
        self.assertEqual(timer_misses(out), [], out)

    def test_flag_on_disabled_timer_still_misses(self):
        # The probe can fail: once re-enabled by intent, a dead timer is a MISS.
        rc, out = run_verify("off", intended_on=1)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("MISS not enabled: %s (disabled)" % TIMER, out)
        self.assertIn("MISS not active: %s (inactive)" % TIMER, out)
        self.assertIn("MISS not scheduled: %s (SubState=dead)" % TIMER, out)

    def test_flag_on_live_timer_no_state_miss(self):
        _, out = run_verify("on", intended_on=1)
        self.assertEqual(timer_misses(out), [], out)

    def test_shipped_intent_is_off(self):
        text = open(INSTALL).read()
        self.assertRegex(text, r"(?m)^INTENDED_ON=0$")
        self.assertIn('vfile "$SRC/minerva_guard.py"', text)
        self.assertIn('vunit_file "$SRC/lumen-minerva-guard.timer"', text)

    def test_install_enable_is_gated(self):
        # Running the bundle as root must not undo the subtraction.
        text = open(INSTALL).read()
        self.assertRegex(text, r'if \[ "\$INTENDED_ON" = 1 \]; then\n\s*systemctl enable --now lumen-minerva-guard\.timer')
        self.assertEqual(text.count("systemctl enable --now"), 1)

    def test_readme_not_stale(self):
        text = open(os.path.join(OPS, BUNDLE, "README.md")).read()
        self.assertNotIn("runs `minerva_guard.py run` every 6h", text)
        self.assertIn("intended-off", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
