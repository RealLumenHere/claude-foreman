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
LAUNCHERS = ["gitea", "postgres", "fleet_state", "docker", "grafana", "playwright", "stealth_browser", "council"]


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

    def test_every_launcher_caps_results_and_third_party_ones_are_marked_untrusted(self):
        """wq#4942 (audit F2): G11 size cap on all, G10 untrusted wrapper on servers returning third-party text."""
        untrusted = {"gitea", "docker", "grafana", "playwright", "stealth_browser", "council"}
        for name in LAUNCHERS:
            with self.subTest(launcher=name):
                argv = guard_argv(emit(name))
                self.assertIn("--max-result-bytes", argv, f"{name}: guard runs without a result size cap")
                self.assertEqual(argv[argv.index("--max-result-bytes") + 1], "524288")
                self.assertEqual("--untrusted-output" in argv, name in untrusted, name)

    def test_wq4888_launchers_hide_code_and_file_tools_and_pin_spend_and_sandbox(self):
        stealth = guard_argv(emit("stealth_browser"))
        allow = set(stealth[stealth.index("--allow") + 1].split(","))
        self.assertEqual(len(allow), 19)
        self.assertFalse({"execute_script", "file_upload"} & allow)
        self.assertIn("spawn_browser:sandbox?=True|true", stealth)
        self.assertIn("navigate:url", stealth)
        council = guard_argv(emit("council"))
        self.assertIn("ask_deepseek:model?=flash|pro", council)
        self.assertTrue(emit("stealth_browser").rstrip().endswith("lumen_stealth_mcp.py --minimal"))
        self.assertTrue(emit("council").rstrip().endswith("/usr/bin/node /home/lumen/council/src/server.js"))

    def test_launchers_parse(self):
        for name in LAUNCHERS:
            with self.subTest(launcher=name):
                subprocess.run(["bash", "-n"], input=emit(name), check=True, text=True)


if __name__ == "__main__":
    unittest.main()
