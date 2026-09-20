#!/usr/bin/env python3
"""wq#2282 — install.sh --verify for the wq#2265 simpill-watch bundle.

Runs standalone: python3 ops/tests/test_wq2265_simpill_watch_verify.py
`systemctl` is stubbed on PATH, so nothing is started, enabled or reloaded; the unit
files, baseline state and simpill_repos.json are read from the box as the bundle does.

Why this exists: wq#2265 shipped a bundle with no --verify, so "merged" was the only
evidence the timer was live (the same gap wq#2259 found for wq#2242). The handler has
to tell a live producer from an installed-but-disabled one, and must never install.
"""
import os
import subprocess
import tempfile
import textwrap
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
INSTALL = os.path.join(REPO, "ops", "wq2265-simpill-watch", "install.sh")
LIVE_UNITS = ("service", "timer")


def _run(enabled, active):
    """Run `install.sh --verify` with systemctl stubbed; return (rc, stdout, calls)."""
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "calls.log")
        stub = os.path.join(tmp, "systemctl")
        with open(stub, "w") as fh:
            fh.write(textwrap.dedent("""\
                #!/usr/bin/env bash
                echo "$*" >> "$STUB_LOG"
                case "$1" in
                  is-enabled) echo "$STUB_ENABLED" ;;
                  is-active)  echo "$STUB_ACTIVE" ;;
                esac
                """))
        os.chmod(stub, 0o755)
        env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"],
                   STUB_LOG=log, STUB_ENABLED=enabled, STUB_ACTIVE=active)
        proc = subprocess.run(["bash", INSTALL, "--verify"], env=env,
                              capture_output=True, text=True, timeout=60)
        calls = []
        if os.path.exists(log):
            with open(log) as fh:
                calls = fh.read().splitlines()
        return proc.returncode, proc.stdout, calls


class SimpillWatchVerify(unittest.TestCase):
    def test_handler_precedes_install_steps(self):
        with open(INSTALL) as fh:
            text = fh.read()
        self.assertIn('if [ "${1:-}" = --verify ]; then', text)
        self.assertLess(text.index("--verify"), text.index("install -D"))

    @unittest.skipUnless(
        all(os.path.exists("/etc/systemd/system/lumen-simpill-watch." + u) for u in LIVE_UNITS),
        "simpill-watch units not installed on this host")
    def test_live_box_passes_and_is_read_only(self):
        rc, out, calls = _run("enabled", "active")
        self.assertEqual(rc, 0, out)
        self.assertIn("VERIFY OK", out)
        self.assertTrue(calls)
        for call in calls:
            self.assertTrue(call.startswith(("is-enabled", "is-active")), call)

    def test_disabled_inactive_timer_fails(self):
        rc, out, calls = _run("disabled", "inactive")
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS not enabled: lumen-simpill-watch.timer", out)
        self.assertIn("MISS not active: lumen-simpill-watch.timer", out)
        self.assertNotIn("VERIFY OK", out)
        for call in calls:
            self.assertTrue(call.startswith(("is-enabled", "is-active")), call)


if __name__ == "__main__":
    unittest.main()
