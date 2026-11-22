#!/usr/bin/env python3
"""wq#5322: wq1640 install.sh must keep the intended-off prefs-block-check.timer off.

Port of wq#5273's pattern (ops/tests/test_wq5273_provider_watch_intended_off.py).
prefs-block-check.timer was disabled on purpose by the 2026-10-08 subtraction
(/home/lumen/scratch/subtraction/timers-disable-list.txt:59). wq#5256 dropped the
venabled/vactive asserts from --verify, but left install.sh's
`systemctl enable --now` unguarded and made --verify blind to a re-enable.

systemctl is shimmed per case and logs every call, so no case touches the live unit.
The module/unit-file checks still compare against live paths (read-only), so only the
timer-state lines are asserted. The install path is cut off right after the gate in the
temp copy (step 1 would otherwise write /home/lumen/minerva).
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
OPS = os.path.join(REPO, "ops")
BUNDLE = "wq1640-lumen-state-render-frank-side-hardware-q-a3"
UNIT = "prefs-block-check.timer"
FIRST_STEP = 'python3 -m pytest -q "$HERE/test_prefs_block.py"\n'


def read(path):
    with open(path) as fh:
        return fh.read()


def shim(enabled, active, sub="waiting", nxt="123456789"):
    return ('#!/bin/sh\necho "$@" >> "$SHIM_LOG"\ncase "$1" in\n'
            f'  is-enabled) echo {enabled}; [ {enabled} = enabled ] ;;\n'
            f'  is-active) echo {active}; [ {active} = active ] ;;\n'
            '  show) case "$4" in\n'  # show UNIT -p PROP --value
            f'      SubState) echo {sub} ;;\n'
            f'      *) echo {nxt} ;;\n'
            '    esac ;;\n'
            '  *) exit 0 ;;\nesac\n')


def run(args, enabled="disabled", active="inactive", sub="waiting", nxt="123456789",
        text=None, env_extra=None):
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops")
        os.mkdir(ops)
        shutil.copy(os.path.join(OPS, "_verify.sh"), ops)
        shutil.copytree(os.path.join(OPS, BUNDLE), os.path.join(ops, BUNDLE),
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        script = os.path.join(ops, BUNDLE, "install.sh")
        src = read(script) if text is None else text
        # Never let a case reach the real install steps.
        assert src.count(FIRST_STEP) == 1
        src = src.replace(FIRST_STEP, 'echo PAST_GATE; exit 0\n', 1)
        with open(script, "w") as fh:
            fh.write(src)
        shims = os.path.join(tmp, "shims")
        os.mkdir(shims)
        for name in ("systemctl", "sudo"):
            with open(os.path.join(shims, name), "w") as fh:
                fh.write(shim(enabled, active, sub, nxt) if name == "systemctl"
                         else '#!/bin/sh\necho "sudo $@" >> "$SHIM_LOG"\nexit 0\n')
            os.chmod(os.path.join(shims, name), 0o755)
        log = os.path.join(tmp, "systemctl.log")
        open(log, "w").close()
        env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""), SHIM_LOG=log)
        env.pop("PREFS_BLOCK_REENABLE", None)
        env.update(env_extra or {})
        out = subprocess.run(["bash", script] + args, capture_output=True, text=True,
                             timeout=120, env=env)
        return out.returncode, out.stdout + out.stderr, read(log)


class PrefsBlockIntendedOffTest(unittest.TestCase):
    def test_off_prints_off_and_no_timer_miss(self):
        _, out, _ = run(["--verify"])
        self.assertIn(f"OFF {UNIT} disabled/inactive", out)
        self.assertEqual([l for l in out.splitlines() if l.startswith("MISS") and UNIT in l
                          and "/etc/systemd/system" not in l], [])
        self.assertNotIn("REENABLED", out)

    def test_disabled_but_active_is_a_miss(self):
        rc, out, _ = run(["--verify"], enabled="disabled", active="active")
        self.assertNotEqual(rc, 0)
        self.assertIn(f"MISS {UNIT} disabled but active", out)

    def test_reenable_is_reported_and_on_contract_enforced(self):
        # Falsifiability: a re-enabled timer is visible, and one that is enabled but not
        # actually scheduled fails --verify.
        rc, out, _ = run(["--verify"], enabled="enabled", active="active",
                         sub="elapsed", nxt="infinity")
        self.assertNotEqual(rc, 0)
        self.assertIn(f"REENABLED {UNIT} enabled", out)
        self.assertIn(f"not scheduled: {UNIT}", out)
        self.assertNotIn(f"OFF {UNIT}", out)

    def test_reenable_properly_scheduled_raises_no_timer_miss(self):
        _, out, _ = run(["--verify"], enabled="enabled", active="active")
        self.assertIn(f"REENABLED {UNIT} enabled", out)
        self.assertNotIn(f"not active: {UNIT}", out)
        self.assertNotIn(f"not scheduled: {UNIT}", out)

    def test_wq5256_verify_was_blind_to_reenable(self):
        # The pre-wq5322 verify block (32f2a028) prints nothing about the timer state, so
        # the REENABLED/OFF assertions above could not pass against it.
        old = subprocess.run(["git", "-C", REPO, "show", f"32f2a028:ops/{BUNDLE}/install.sh"],
                             capture_output=True, text=True, timeout=30)
        if old.returncode != 0:
            self.skipTest("32f2a028 not reachable in this clone")
        _, out, _ = run(["--verify"], enabled="enabled", active="active", text=old.stdout)
        self.assertNotIn("REENABLED", out)
        _, out, _ = run(["--verify"], text=old.stdout)
        self.assertNotIn(f"OFF {UNIT}", out)

    def test_install_refuses_without_explicit_reenable(self):
        rc, out, calls = run([])
        self.assertEqual(rc, 2)
        self.assertIn("PREFS_BLOCK_REENABLE=1", out)
        self.assertNotIn("PAST_GATE", out)
        self.assertNotIn("enable", calls)
        self.assertNotIn("sudo", calls)

    def test_install_refuses_for_non_1_values(self):
        rc, out, _ = run([], env_extra={"PREFS_BLOCK_REENABLE": "yes"})
        self.assertEqual(rc, 2)
        self.assertNotIn("PAST_GATE", out)

    def test_install_with_reenable_passes_the_gate(self):
        rc, out, _ = run([], env_extra={"PREFS_BLOCK_REENABLE": "1"})
        self.assertEqual(rc, 0)
        self.assertIn("PAST_GATE", out)

    def test_old_install_had_no_gate(self):
        # Falsifiability of the gate test: the pre-wq5322 script goes straight past it.
        old = subprocess.run(["git", "-C", REPO, "show", f"32f2a028:ops/{BUNDLE}/install.sh"],
                             capture_output=True, text=True, timeout=30)
        if old.returncode != 0:
            self.skipTest("32f2a028 not reachable in this clone")
        rc, out, _ = run([], text=old.stdout)
        self.assertEqual(rc, 0)
        self.assertIn("PAST_GATE", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
