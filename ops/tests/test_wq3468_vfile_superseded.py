#!/usr/bin/env python3
"""wq#3468: vfile accepts a live file that moved on from the bundle through its own repo.

Before this, vfile was a byte compare. A bundle that shipped a file in a git-tracked live
tree (/home/lumen/bin, /home/lumen/minerva) failed --verify the day a later commit edited
that file, and ops-deploy-drift re-filed it every day after. Redeploying would have reverted
the later commits, so no agent could clear those rows.

Runs standalone: python3 ops/tests/test_wq3468_vfile_superseded.py
Each case sources the real ops/_verify.sh against a throwaway git repo.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

VERIFY_SH = Path(__file__).resolve().parents[1] / "_verify.sh"


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo, rel, body):
    p = Path(repo, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    git(repo, "add", rel)
    git(repo, "commit", "-qm", f"edit {rel}")


def vfile(src, dst):
    script = f'. "{VERIFY_SH}"\nvfile "$1" "$2"\nvdone\n'
    p = subprocess.run(["bash", "-c", script, "vfile", str(src), str(dst)], capture_output=True, text=True)
    return p.returncode, p.stdout


class VfileSuperseded(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.live = root / "live"
        self.live.mkdir()
        git(self.live, "init", "-q")
        self.bundle = root / "bundle" / "tool.py"
        self.bundle.parent.mkdir()
        self.dst = self.live / "bin" / "tool"

    def tearDown(self):
        self.tmp.cleanup()

    def ship(self, body):
        self.bundle.write_text(body)
        commit(self.live, "bin/tool", body)

    def test_byte_identical_still_passes(self):
        self.ship("v1\n")
        rc, out = vfile(self.bundle, self.dst)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("superseded", out)

    def test_a_later_commit_on_the_live_file_passes_as_superseded(self):
        self.ship("v1\n")
        commit(self.live, "bin/tool", "v1\nv2 from a later ticket\n")
        rc, out = vfile(self.bundle, self.dst)
        self.assertEqual(rc, 0, out)
        self.assertIn("OK superseded:", out)

    def test_an_uncommitted_edit_on_top_is_a_miss(self):
        self.ship("v1\n")
        commit(self.live, "bin/tool", "v2\n")
        self.dst.write_text("v2 plus a hand edit\n")
        rc, out = vfile(self.bundle, self.dst)
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS differs:", out)

    def test_a_bundle_newer_than_live_is_a_miss(self):
        """The undeployed case: live is an OLDER version, the bundle's blob was never committed."""
        commit(self.live, "bin/tool", "v1\n")
        self.bundle.write_text("v2 from the bundle, never deployed\n")
        rc, out = vfile(self.bundle, self.dst)
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS differs:", out)

    def test_bundle_content_committed_under_another_path_does_not_count(self):
        commit(self.live, "bin/other", "v1\n")
        commit(self.live, "bin/tool", "v2\n")
        self.bundle.write_text("v1\n")
        rc, out = vfile(self.bundle, self.dst)
        self.assertEqual(rc, 1, out)

    def test_an_untracked_target_keeps_the_byte_compare(self):
        commit(self.live, "README", "x\n")
        self.dst.parent.mkdir(exist_ok=True)
        self.dst.write_text("v2\n")
        self.bundle.write_text("v1\n")
        rc, out = vfile(self.bundle, self.dst)
        self.assertEqual(rc, 1, out)

    def test_a_target_outside_any_repo_keeps_the_byte_compare(self):
        outside = Path(self.tmp.name) / "plain" / "tool"
        outside.parent.mkdir()
        outside.write_text("v2\n")
        self.bundle.write_text("v1\n")
        rc, out = vfile(self.bundle, outside)
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS differs:", out)

    def test_a_symlinked_target_resolves_to_its_repo(self):
        self.ship("v1\n")
        commit(self.live, "bin/tool", "v2\n")
        link = Path(self.tmp.name) / "tool-link"
        link.symlink_to(self.dst)
        rc, out = vfile(self.bundle, link)
        self.assertEqual(rc, 0, out)
        self.assertIn("OK superseded:", out)

    def test_absent_target_is_still_absent(self):
        commit(self.live, "README", "x\n")
        self.bundle.write_text("v1\n")
        rc, out = vfile(self.bundle, self.dst)
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS absent:", out)


if __name__ == "__main__":
    unittest.main()
