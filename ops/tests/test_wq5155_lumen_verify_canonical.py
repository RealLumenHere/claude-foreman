#!/usr/bin/env python3
"""wq#5155 — the wq1613 verifier bundle must not drift from, or roll back, its canonical sources.

lumen-verify is developed in lumen-bin and registry.yaml in minerva; the bundle copies only feed
its tests. The copy once fell 220 lines behind and install.sh would have reverted the live files.

Runs standalone: python3 ops/tests/test_wq5155_lumen_verify_canonical.py
Read-only on live state: compares git objects, and runs install.sh only in a temp copy where it
must refuse before any install step. Skips when the canonical repos are not on this host.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
BUNDLE = os.path.join(REPO, "ops", "wq1613-minerva-verifier-registry")
CANON = [
    ("lumen_verify.py", "/home/lumen/bin", "lumen-verify"),
    ("registry.yaml", "/home/lumen/minerva", "verify/registry.yaml"),
]


def canonical(repo, path):
    out = subprocess.run(["git", "-C", repo, "show", f"origin/main:{path}"],
                         capture_output=True, timeout=30)
    return out.stdout if out.returncode == 0 else None


@unittest.skipUnless(all(os.path.isdir(os.path.join(r, ".git")) for _, r, _ in CANON),
                     "canonical repos not on this host")
class CanonicalCopyTest(unittest.TestCase):
    def test_bundle_copies_match_canonical_main(self):
        for copy, repo, path in CANON:
            with self.subTest(copy=copy):
                want = canonical(repo, path)
                self.assertIsNotNone(want, f"cannot read {repo} origin/main:{path}")
                with open(os.path.join(BUNDLE, copy), "rb") as fh:
                    have = fh.read()
                self.assertEqual(have, want,
                                 f"{copy} drifted from {repo} origin/main:{path}; "
                                 f"run ops/wq1613-minerva-verifier-registry/install.sh --sync and land it")

    def test_install_refuses_a_stale_copy_before_installing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "bundle")
            shutil.copytree(BUNDLE, dst, ignore=shutil.ignore_patterns("__pycache__"))
            with open(os.path.join(dst, "lumen_verify.py"), "a") as fh:
                fh.write("\n# stale\n")
            # Belt and braces: if the refusal ever regressed, every write step would hit a shim
            # that fails instead of touching /home/lumen/bin or systemd.
            shims = os.path.join(tmp, "shims")
            os.mkdir(shims)
            for name in ("install", "sudo", "systemctl", "python3"):
                with open(os.path.join(shims, name), "w") as fh:
                    fh.write("#!/bin/sh\necho shim-$0-called >&2\nexit 97\n")
                os.chmod(os.path.join(shims, name), 0o755)
            env = dict(os.environ, PATH=shims + os.pathsep + os.environ.get("PATH", ""))
            out = subprocess.run(["bash", os.path.join(dst, "install.sh")],
                                 capture_output=True, text=True, timeout=120, env=env)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("REFUSED", out.stderr)
            self.assertIn("lumen_verify.py differs", out.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
