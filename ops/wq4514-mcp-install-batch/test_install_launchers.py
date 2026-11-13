"""wq#4943: every guarded launcher install.sh generates masks its output.

Sources install.sh up to the verify helpers (pins + emitters only, no install, no verify) and
runs each emit_launcher_* in bash. INSTALL_SH overrides the script under test, so the same
probe can be pointed at a pre-fix revision (`git show <rev>:.../install.sh > /tmp/x.sh`).
"""
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALL_SH = Path(os.environ.get("INSTALL_SH", HERE / "install.sh"))
MASKER = "/home/lumen/bin/mask-secrets"
LAUNCHERS = ["gitea", "postgres", "fleet_state", "docker", "grafana", "playwright"]


def emit(name):
    text = INSTALL_SH.read_text()
    head = text[: text.index("# ---------------------------------------------------------------- verify helpers")]
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(head)
    try:
        return subprocess.run(
            ["bash", "-c", f"source {shlex.quote(f.name)}; emit_launcher_{name}"],
            check=True, capture_output=True, text=True,
        ).stdout
    finally:
        os.unlink(f.name)


def guard_argv(launcher):
    """The guard's own argv: the exec line up to the `--` that starts the backend command."""
    body = launcher[launcher.index("exec "):].replace("\\\n", " ")
    argv = shlex.split(body.split("\n", 1)[0])
    return argv[: argv.index("--")]


class LauncherMasking(unittest.TestCase):
    def test_every_launcher_masks_output(self):
        for name in LAUNCHERS:
            with self.subTest(launcher=name):
                argv = guard_argv(emit(name))
                self.assertIn("--mask-output", argv, f"{name}: guard runs without --mask-output")
                self.assertEqual(argv[argv.index("--mask-output") + 1], MASKER)

    def test_launchers_parse(self):
        for name in LAUNCHERS:
            with self.subTest(launcher=name):
                subprocess.run(["bash", "-n"], input=emit(name), check=True, text=True)


if __name__ == "__main__":
    unittest.main()
