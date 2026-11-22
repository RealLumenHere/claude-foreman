#!/usr/bin/env python3
"""wq#2182 — the wq1609 contradiction-scan bundle answers install.sh --verify.

Runs standalone: python3 ops/tests/test_wq1609_scan_verify.py
The stubbed cases put fake `systemctl` and `sudo` first on PATH and log every call, so they
prove the handler is read-only (contract, ops/VERIFY_CONTRACT.md) without touching the
box's units. The live case runs the handler against the real deploy targets.

Why this exists: wq#2176 deployed the scanner with a hand `cp`, because install.sh is not
runnable from a build worker, and the bundle had no --verify. Merged was not live, and
nothing could say whether the timer's script had drifted back behind the repo copy.
"""
import os
import stat
import subprocess
import textwrap
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
INSTALL = os.path.join(REPO, "ops", "wq1609-minerva-contradiction-scan", "install.sh")
LIVE_BIN = "/home/lumen/bin/lumen-contradiction-scan"


def _run(is_enabled="enabled", is_active="active", install_text=None):
    """Run `install.sh --verify` with systemctl/sudo stubbed; return (proc, call_log).

    install_text: run a modified copy of the bundle (falsifiability probes) instead."""
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "calls.log")
        install = INSTALL
        if install_text is not None:
            ops = os.path.join(tmp, "ops")
            bundle = os.path.join(ops, os.path.basename(os.path.dirname(INSTALL)))
            shutil.copytree(os.path.dirname(INSTALL), bundle,
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(os.path.join(REPO, "ops", "_verify.sh"), ops)
            install = os.path.join(bundle, "install.sh")
            with open(install, "w") as fh:
                fh.write(install_text)
        for name, body in (
            ("systemctl", """\
                echo "systemctl $*" >> "$STUB_LOG"
                case "$1" in
                  is-enabled) echo "$STUB_ENABLED";;
                  is-active) echo "$STUB_ACTIVE";;
                esac
            """),
            ("sudo", """\
                echo "sudo $*" >> "$STUB_LOG"
            """),
        ):
            path = os.path.join(tmp, name)
            with open(path, "w") as fh:
                fh.write("#!/usr/bin/env bash\n" + textwrap.dedent(body))
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"],
                   STUB_LOG=log, STUB_ENABLED=is_enabled, STUB_ACTIVE=is_active)
        proc = subprocess.run(["bash", install, "--verify"], env=env,
                              capture_output=True, text=True)
        calls = open(log).read() if os.path.exists(log) else ""
    return proc, calls


class VerifyHandler(unittest.TestCase):
    def test_marker_precedes_every_install_step(self):
        """ops-deploy-drift only runs --verify where it sees the marker, and a script that
        installed before reaching it would install on a verify call."""
        with open(INSTALL) as fh:
            src = fh.read()
        marker = src.index('= --verify ]')
        for step in ("pytest", "install -m", "systemctl daemon-reload", "enable --now"):
            self.assertLess(marker, src.index(step), step)

    def test_verify_is_read_only(self):
        proc, calls = _run()
        for m in ("sudo", "daemon-reload", "enable --now", "start"):
            self.assertNotIn(m, calls, proc.stdout)
        self.assertNotIn("passed", proc.stdout, "pytest must not run under --verify")

    # wq#5336: the timer is intended-off since wq#5255 (c866b57d; install.sh --verify
    # comment cites timers-disable-list.txt:25), so --verify must neither read nor demand
    # its state. These replace the pre-wq#5255 "disabled/inactive is a MISS" tests.
    def test_does_not_read_timer_state(self):
        _, calls = _run()
        self.assertNotIn("is-enabled lumen-contradiction-scan.timer", calls)
        self.assertNotIn("is-active lumen-contradiction-scan.timer", calls)

    def test_intended_off_timer_is_not_a_miss(self):
        proc, _ = _run(is_enabled="disabled", is_active="inactive")
        self.assertNotIn("not enabled: lumen-contradiction-scan.timer", proc.stdout)
        self.assertNotIn("not active: lumen-contradiction-scan.timer", proc.stdout)

    def test_old_asserts_would_have_failed_under_same_stub(self):
        # Falsifiability: re-insert the pre-wq#5255 asserts; the same stub must flag them.
        with open(INSTALL) as fh:
            text = fh.read()
        unit = "lumen-contradiction-scan.timer"
        old = text.replace("  vdone\n", f"  venabled {unit}\n  vactive {unit}\n  vdone\n", 1)
        self.assertNotEqual(old, text)
        proc, calls = _run(is_enabled="disabled", is_active="inactive", install_text=old)
        self.assertIn(f"is-enabled {unit}", calls)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(f"MISS not enabled: {unit}", proc.stdout)
        self.assertIn(f"MISS not active: {unit}", proc.stdout)

    @unittest.skipUnless(os.path.exists(LIVE_BIN), "scanner not deployed on this host")
    def test_live_targets_verdict_is_well_formed(self):
        """Real files, stubbed systemctl (active/enabled): only the copies can miss.

        Asserts the shape, not OK, so a legitimately stale box still fails the *drift
        check* rather than this test; the message carries the verdict either way.
        """
        proc, _ = _run()
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        self.assertRegex(proc.stdout, r"VERIFY (OK|FAIL \(\d+\))")
        print("LIVE", proc.returncode, proc.stdout.strip().replace("\n", " | "))


if __name__ == "__main__":
    unittest.main()
