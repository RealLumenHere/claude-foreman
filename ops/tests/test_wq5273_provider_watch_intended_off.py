#!/usr/bin/env python3
"""wq#5273: wq3336 provider-watch install.sh --verify must agree with the 2026-10-08 subtraction.

provider-watch.timer was disabled on purpose by the Timer Massacre (wq#3648):
/home/lumen/scratch/subtraction/timers-disable-phase2.txt:51, phase2b-results.txt:52.
--verify asserted venabled/vactive on it plus a byte-match of the (dormant) payload, so it
failed forever and filed an ops-root-apply telling the reader to run install.sh as root,
whose `systemctl enable --now` would undo the subtraction.

systemctl is shimmed per case; BIN_DST is rewritten to a temp file so the payload input is
pinned (never the live /home/lumen/bin copy). Unit-file checks still compare against live
paths, so only timer-state and payload lines are asserted.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq3336-provider-watch"
UNIT = "provider-watch.timer"
LIVE_BIN = "BIN_DST=/home/lumen/bin/provider-watch"


def read(path):
    with open(path) as fh:
        return fh.read()


def shim(enabled, active):
    return ('#!/bin/sh\necho "$@" >> "$SHIM_LOG"\ncase "$1" in\n'
            f'  is-enabled) echo {enabled}; [ {enabled} = enabled ] ;;\n'
            f'  is-active) echo {active}; [ {active} = active ] ;;\n'
            '  show) echo; exit 0 ;;\n'
            '  *) exit 0 ;;\nesac\n')


def run(args, enabled="disabled", active="inactive", payload="same", text=None, env_extra=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        shutil.copytree(os.path.join(OPS, BUNDLE), os.path.join(ops, BUNDLE),
                        ignore=shutil.ignore_patterns("__pycache__"))
        script = os.path.join(ops, BUNDLE, "install.sh")
        src = read(script) if text is None else text
        bin_dst = os.path.join(tmp, "provider-watch")
        assert LIVE_BIN in src
        src = src.replace(LIVE_BIN, "BIN_DST=" + bin_dst, 1)
        with open(script, "w") as fh:
            fh.write(src)
        if payload == "same":
            shutil.copy(os.path.join(OPS, BUNDLE, "provider_watch.py"), bin_dst)
        elif payload == "stale":
            with open(bin_dst, "w") as fh:
                fh.write("#!/usr/bin/env python3\n# stale pre-wq3991 build\n")
        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        with open(os.path.join(shims, "systemctl"), "w") as fh:
            fh.write(shim(enabled, active))
        os.chmod(os.path.join(shims, "systemctl"), 0o755)
        log = os.path.join(tmp, "systemctl.log")
        open(log, "w").close()
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""), SHIM_LOG=log)
        env.pop("PROVIDER_WATCH_REENABLE", None)
        env.update(env_extra or {})
        out = subprocess.run(["bash", script] + args, capture_output=True, text=True,
                             timeout=120, env=env)
        return out.returncode, out.stdout + out.stderr, read(log)


class ProviderWatchIntendedOffTest(unittest.TestCase):
    def test_off_with_stale_payload_raises_no_timer_or_payload_miss(self):
        _, out, _ = run(["--verify"], payload="stale")
        self.assertNotIn(f"not enabled: {UNIT}", out)
        self.assertNotIn(f"not active: {UNIT}", out)
        self.assertEqual([l for l in out.splitlines()
                          if l.startswith("MISS") and "provider_watch.py" in l], [])
        self.assertIn(f"OFF {UNIT} disabled/inactive", out)
        self.assertIn("OFF dormant payload differs", out)

    def test_off_with_matching_payload_prints_no_drift(self):
        _, out, _ = run(["--verify"], payload="same")
        self.assertIn(f"OFF {UNIT} disabled/inactive", out)
        self.assertNotIn("dormant payload", out)

    def test_disabled_but_active_is_a_miss(self):
        _, out, _ = run(["--verify"], enabled="disabled", active="active")
        self.assertIn(f"MISS {UNIT} disabled but active", out)

    def test_reenabled_enforces_payload_and_schedule(self):
        # Falsifiability: once someone re-enables the timer, a stale payload and an
        # unscheduled timer must both fail --verify.
        rc, out, _ = run(["--verify"], enabled="enabled", active="active", payload="stale")
        self.assertNotEqual(rc, 0)
        self.assertIn("provider-watch (from provider_watch.py)", out)
        self.assertIn(f"not scheduled: {UNIT}", out)

    def test_old_asserts_would_have_failed_under_same_shim(self):
        text = read(os.path.join(OPS, BUNDLE, "install.sh"))
        old = text.replace("  vdone\n", f"  venabled {UNIT}\n  vactive {UNIT}\n  vdone\n", 1)
        self.assertNotEqual(old, text)
        _, out, _ = run(["--verify"], text=old)
        self.assertIn(f"not enabled: {UNIT}", out)
        self.assertIn(f"not active: {UNIT}", out)

    def test_install_refuses_without_explicit_reenable(self):
        rc, out, calls = run([])
        self.assertEqual(rc, 2)
        self.assertIn("PROVIDER_WATCH_REENABLE=1", out)
        self.assertNotIn("enable", calls)

    def test_install_with_reenable_passes_the_gate(self):
        # Past the gate a non-root run stops at the root check (exit 1), not the gate.
        if os.geteuid() == 0:
            self.skipTest("root would really install")
        rc, out, _ = run([], env_extra={"PROVIDER_WATCH_REENABLE": "1"})
        self.assertEqual(rc, 1)
        self.assertIn("must run as root", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
