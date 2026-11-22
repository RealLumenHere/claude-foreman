#!/usr/bin/env python3
"""wq#5275: wq394 install.sh --verify must agree with the 2026-10-08 subtraction.

Subtraction 1G (scratch/subtraction/REPORT.md:48) stopped+disabled netdata.service and
wq394-netdata-sampler.timer on purpose. --verify asserted enabled/active on both and the
sampler payload, so it failed forever and filed ops-root-apply tickets whose "fix" (root
install) would undo the decision. Now (same shape as wq#5173):
  unit disabled + inactive  -> OFF line, no MISS (payload not checked for the timer)
  unit disabled + active    -> MISS (decision bypassed)
  unit enabled              -> enabled/active/scheduled (+ payload for the timer) as before
and the install path refuses (exit 2) unless WQ394_REENABLE=1.
systemctl is shimmed per unit; nothing is written.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq394-netdata-canary"
INSTALL = os.path.join(OPS, BUNDLE, "install.sh")
TIMER = "wq394-netdata-sampler.timer"
UNITS_LIVE = all(os.path.exists(f"/etc/systemd/system/wq394-netdata-sampler.{s}")
                 for s in ("service", "timer"))

SHIM = r"""#!/bin/sh
# args: is-enabled [--quiet] U | is-active U | show U -p P --value
verb="$1"; shift
[ "$1" = --quiet ] && { q=1; shift; }
u="$1"
case "$u" in netdata.service) en="$ND_EN"; act="$ND_ACT" ;; *) en="$T_EN"; act="$T_ACT" ;; esac
case "$verb" in
  is-enabled) [ -n "$q" ] || echo "$en"; [ "$en" = enabled ] && exit 0; exit 1 ;;
  is-active) echo "$act"; [ "$act" = active ] && exit 0; exit 3 ;;
  show) case "$*" in *SubState*) echo "$T_SUB" ;; *Monotonic*) echo "$T_MONO" ;;
                     *Realtime*) echo "$T_REAL" ;; *) echo ;; esac; exit 0 ;;
  *) exit 0 ;;
esac
"""


def run(args, t_en="disabled", t_act="inactive", nd_en="disabled", nd_act="inactive",
        sub="dead", mono="infinity", real="", extra_env=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        shutil.copytree(os.path.join(OPS, BUNDLE), os.path.join(ops, BUNDLE),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        with open(os.path.join(shims, "systemctl"), "w") as fh:
            fh.write(SHIM)
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        # A regressed install path (no refusal) must die at its first step, not download
        # and run netdata's kickstart: curl/sudo/cp/sed/mkdir are shimmed to fail.
        for tool in ("curl", "sudo", "cp", "sed", "mkdir", "chown", "chmod"):
            p = os.path.join(shims, tool)
            with open(p, "w") as fh:
                fh.write("#!/bin/sh\necho \"shim: $0 refused\" >&2\nexit 97\n")
            os.chmod(p, 0o755)
        env = dict(os.environ)
        env.pop("WQ394_REENABLE", None)
        env.update(PATH=shims + os.pathsep + env.get("PATH", ""), T_EN=t_en, T_ACT=t_act,
                   ND_EN=nd_en, ND_ACT=nd_act, T_SUB=sub, T_MONO=mono, T_REAL=real)
        env.update(extra_env or {})
        return subprocess.run(["bash", os.path.join(ops, BUNDLE, "install.sh")] + args,
                              capture_output=True, text=True, env=env, timeout=60)


def misses(r):
    return [l for l in r.stdout.splitlines() if l.startswith("MISS")]


@unittest.skipUnless(UNITS_LIVE, "wq394 unit files not installed on this host")
class Wq394Verify(unittest.TestCase):
    def test_intended_off_state_verifies_ok(self):
        # The live state on lumen since 2026-10-08: both disabled + inactive.
        r = run(["--verify"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(misses(r), [])
        self.assertIn(f"OFF {TIMER} disabled", r.stdout)
        self.assertIn("OFF netdata.service disabled", r.stdout)
        self.assertIn("VERIFY OK", r.stdout)

    def test_disabled_but_running_timer_is_a_miss(self):
        r = run(["--verify"], t_act="active")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"MISS {TIMER} disabled but active", r.stdout)

    def test_disabled_but_running_netdata_is_a_miss(self):
        r = run(["--verify"], nd_act="active")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("MISS netdata.service disabled but active", r.stdout)

    def test_enabled_timer_gets_full_checks(self):
        # Enabled but dead + unscheduled: every run-state check must fire.
        r = run(["--verify"], t_en="enabled", t_act="inactive", sub="elapsed")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"MISS not active: {TIMER} (inactive)", r.stdout)
        self.assertIn(f"MISS not scheduled: {TIMER}", r.stdout)

    def test_enabled_netdata_must_be_active(self):
        r = run(["--verify"], nd_en="enabled", nd_act="inactive")
        self.assertIn("MISS not active: netdata.service (inactive)", r.stdout)

    def test_install_refuses_without_reenable(self):
        r = run(["100.64.0.1"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("WQ394_REENABLE=1", r.stderr)
        self.assertNotIn("netdata canary live", r.stdout)
        self.assertNotIn("shim:", r.stderr)  # refused before touching any install step

    def test_install_with_reenable_proceeds_past_refusal(self):
        # Opt-in reaches step 1 (curl, shimmed to fail) instead of the refusal.
        r = run(["100.64.0.1"], extra_env={"WQ394_REENABLE": "1"})
        self.assertEqual(r.returncode, 97, r.stdout + r.stderr)
        self.assertIn("shim:", r.stderr)
        self.assertNotIn("intended-off", r.stderr)


class Wq394VerifyBlock(unittest.TestCase):
    def block(self):
        m = re.search(r'if \[ "\$\{1:-\}" = --verify \]; then\n(.*?)\n  vdone\nfi', open(INSTALL).read(), re.S)
        self.assertTrue(m, "no --verify block")
        return m.group(1)

    def test_payload_checked_only_inside_enabled_branch(self):
        block = self.block()
        gate = block.index(f"if systemctl is-enabled --quiet {TIMER}")
        for f in ("sampler.py", "analyze.py"):
            self.assertGreater(block.index(f'vfile      "$SRC/{f}"'), gate)

    def test_unit_files_still_checked_unconditionally(self):
        block = self.block()
        gate = block.index(f"if systemctl is-enabled --quiet {TIMER}")
        for s in ("service", "timer"):
            self.assertLess(block.index(f'vunit_file "$SRC/wq394-netdata-sampler.{s}"'), gate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
