"""wq#5314: wq2086 presentation-stability install.sh must not re-enable its intended-off timer.

lumen-presentation-stability.timer was disabled on purpose by the 2026-10-08 subtraction
(/home/lumen/scratch/subtraction/timers-disable-list.txt:7). wq#5258 stopped --verify demanding
it enabled+active, but install.sh still ran `systemctl enable --now` as root and its unprivileged
branch still listed that as an owed step, so a root replay undid the subtraction. Same shape as
wq#5266 (iptables drift-guard) and wq#5267 (ir-triage): an in-file intent flag,
PRESENTATION_STABILITY_ON, gates install and verify.

Everything runs in a sandbox copy of the bundle: BIN, UNIT_DIR and BAK are rewritten to temp
paths, the two precheck paths to temp files, PSTAB_ENABLED=0 makes the health `--dry-run` skip
the live corpus, and systemctl is a shim that logs every call and reports SHIM_STATE.
Nothing on the host is touched.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq2086-presentation-stability"
INSTALL = os.path.join(OPS, BUNDLE, "install.sh")
TIMER = "lumen-presentation-stability.timer"
FLAG = "PRESENTATION_STABILITY_ON"

SHIM = r"""#!/bin/sh
echo "$*" >> "$SHIM_LOG"
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


def _sub1(text, pattern, repl):
    text, n = re.subn(pattern, lambda _m: repl, text, flags=re.M)
    assert n == 1, (pattern, n)
    return text


def run_install(args=(), state="off", flag_on=None, unit_dir_writable=True):
    """Run a sandboxed install.sh. Returns (rc, output, systemctl call lines, unit_dir)."""
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        shutil.copytree(os.path.join(OPS, "wq2856-atomic-install"), os.path.join(ops, "wq2856-atomic-install"),
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        dst = os.path.join(ops, BUNDLE)
        shutil.copytree(os.path.join(OPS, BUNDLE), dst,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"))
        unit_dir = os.path.join(tmp, "units") if unit_dir_writable else os.path.join(tmp, "absent", "units")
        if unit_dir_writable:
            os.mkdir(unit_dir)
        os.mkdir(os.path.join(tmp, "bin"))
        verify_py = os.path.join(tmp, "minerva_verify.py")
        db = os.path.join(tmp, "minerva.db")
        for p in (verify_py, db):
            open(p, "w").close()

        path = os.path.join(dst, "install.sh")
        text = open(path).read()
        text = _sub1(text, r"^BIN=.*$", "BIN=" + os.path.join(tmp, "bin", "minerva-presentation-stability"))
        text = _sub1(text, r"^UNIT_DIR=.*$", "UNIT_DIR=" + unit_dir)
        text = _sub1(text, r"^BAK=.*$", "BAK=" + os.path.join(tmp, "bak"))
        text = text.replace("/home/lumen/minerva/minerva_verify.py", verify_py)
        text = text.replace("/home/lumen/minerva/minerva.db", db)
        if flag_on is not None:
            text = _sub1(text, r"^%s=\d$" % FLAG, "%s=%d" % (FLAG, flag_on))
        with open(path, "w") as fh:
            fh.write(text)

        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        with open(os.path.join(shims, "systemctl"), "w") as fh:
            fh.write(SHIM)
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        log = os.path.join(tmp, "systemctl.log")
        open(log, "w").close()
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""),
                   SHIM_STATE=state, SHIM_LOG=log, PSTAB_ENABLED="0")
        p = subprocess.run(["bash", path, *args], capture_output=True, text=True, timeout=120, env=env)
        calls = open(log).read().splitlines()
        installed = sorted(os.listdir(unit_dir)) if unit_dir_writable else []
        return p.returncode, p.stdout + p.stderr, calls, installed


def timer_misses(out):
    return [l for l in out.splitlines() if l.startswith("MISS") and (TIMER + " (") in l]


def enables(calls):
    return [c for c in calls if c.split()[:1] in (["enable"], ["start"], ["restart"])]


class IntendedOffInstall(unittest.TestCase):
    def test_root_replay_does_not_enable_timer(self):
        # The regression: install.sh as root (unit dir writable) ran `enable --now`.
        rc, out, calls, installed = run_install()
        self.assertEqual(rc, 0, out)
        self.assertEqual(installed, ["lumen-presentation-stability.service", TIMER], out)
        self.assertIn("daemon-reload", calls)
        self.assertEqual(enables(calls), [], calls)
        self.assertIn("intended-off", out)

    def test_flag_on_root_install_enables_timer(self):
        # The probe can fail: with the intent flipped, install must enable+start the timer.
        rc, out, calls, _ = run_install(flag_on=1)
        self.assertEqual(rc, 0, out)
        self.assertIn("enable --now " + TIMER, calls)

    def test_unprivileged_branch_does_not_owe_enable(self):
        rc, out, calls, _ = run_install(unit_dir_writable=False)
        self.assertEqual(rc, 3, out)
        self.assertNotIn("systemctl enable --now", out)
        self.assertIn("do NOT enable", out)
        self.assertEqual(enables(calls), [], calls)

    def test_flag_on_unprivileged_branch_owes_enable(self):
        rc, out, _, _ = run_install(unit_dir_writable=False, flag_on=1)
        self.assertEqual(rc, 3, out)
        self.assertIn("systemctl enable --now " + TIMER, out)


class IntendedOffVerify(unittest.TestCase):
    def test_disabled_timer_raises_no_state_miss(self):
        _, out, calls, _ = run_install(["--verify"], state="off")
        self.assertEqual(timer_misses(out), [], out)
        self.assertEqual(enables(calls), [], calls)

    def test_flag_on_disabled_timer_misses(self):
        rc, out, _, _ = run_install(["--verify"], state="off", flag_on=1)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("MISS not enabled: %s (disabled)" % TIMER, out)
        self.assertIn("MISS not active: %s (inactive)" % TIMER, out)
        self.assertIn("MISS not scheduled: %s (SubState=dead)" % TIMER, out)

    def test_flag_on_live_timer_no_state_miss(self):
        _, out, _, _ = run_install(["--verify"], state="on", flag_on=1)
        self.assertEqual(timer_misses(out), [], out)


class ShippedIntent(unittest.TestCase):
    def test_shipped_intent_is_off(self):
        text = open(INSTALL).read()
        self.assertRegex(text, r"(?m)^%s=0$" % FLAG)

    def test_every_enable_is_gated(self):
        # Each `systemctl enable --now` line (run or printed) sits right under the flag check.
        lines = open(INSTALL).read().splitlines()
        hits = [i for i, l in enumerate(lines)
                if "systemctl enable --now" in l and not l.lstrip().startswith("#")]
        self.assertEqual(len(hits), 2, hits)
        for i in hits:
            self.assertRegex(lines[i - 1], r'^\s*if \[ "\$%s" = 1 \]; then$' % FLAG, (i, lines[i]))

    @unittest.skipUnless(shutil.which("systemctl") and os.path.exists("/etc/systemd/system/" + TIMER),
                         "needs the live host")
    def test_live_host_has_no_timer_state_miss(self):
        p = subprocess.run(["bash", INSTALL, "--verify"], capture_output=True, text=True, timeout=120)
        self.assertEqual(timer_misses(p.stdout + p.stderr), [], p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
