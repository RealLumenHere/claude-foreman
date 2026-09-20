import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import remote_credential_sweep as rcs

FAKE = "ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE0000"
OPAQUE = "0123456789abcdef0123456789abcdef01234567"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def init_repo(path, url, key="url", name="lumenfork"):
    os.makedirs(path, exist_ok=True)
    git("init", "-q", cwd=path)
    git("config", f"remote.{name}.{key}", url, cwd=path)


class StripTests(unittest.TestCase):
    def test_strips_token_only_userinfo(self):
        self.assertEqual(rcs.strip_userinfo(f"https://{FAKE}@github.com/o/r.git"), "https://github.com/o/r.git")

    def test_strips_user_pass_opaque_token(self):
        self.assertEqual(rcs.strip_userinfo(f"https://u:{OPAQUE}@host/o/r"), "https://host/o/r")

    def test_clean_and_ssh_urls_untouched(self):
        for u in ("https://github.com/o/r.git", "git@github.com:o/r.git", "ssh://git@host/o/r"):
            self.assertIsNone(rcs.strip_userinfo(u))
            self.assertFalse(rcs.has_credential(u))

    def test_prefix_token_without_userinfo_still_flagged(self):
        self.assertTrue(rcs.has_credential(f"https://github.com/o/r.git?t={FAKE}"))


class ScanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def run_scan(self, fix=False):
        out = io.StringIO()
        return len(rcs.scan(self.root, fix=fix, out=out)), out.getvalue()

    def test_finds_deep_clone_and_never_prints_secret(self):
        deep = os.path.join(self.root, "a", "b", "c", "d", "e", "f", "simpill-pr-api")
        init_repo(deep, f"https://{FAKE}@github.com/o/r.git")
        n, out = self.run_scan()
        self.assertEqual(n, 1)
        self.assertIn("remote=lumenfork", out)
        self.assertNotIn(FAKE, out)
        self.assertNotIn("github.com", out)

    def test_opaque_token_and_pushurl_found(self):
        init_repo(os.path.join(self.root, "r1"), f"https://u:{OPAQUE}@host/o/r", key="pushurl")
        n, out = self.run_scan()
        self.assertEqual(n, 1)
        self.assertNotIn(OPAQUE, out)

    def test_clean_repo_reports_nothing(self):
        init_repo(os.path.join(self.root, "ok"), "https://github.com/o/r.git")
        n, _ = self.run_scan()
        self.assertEqual(n, 0)

    def test_linked_worktree_resolves_to_shared_config_once(self):
        main = os.path.join(self.root, "main")
        init_repo(main, f"https://{FAKE}@github.com/o/r.git")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "x", cwd=main)
        git("worktree", "add", "-q", os.path.join(self.root, "wt"), cwd=main)
        n, out = self.run_scan()
        self.assertEqual(n, 1)
        self.assertEqual(out.count("EMBEDDED"), 1)

    def test_bare_repo_found(self):
        bare = os.path.join(self.root, "bare.git")
        os.makedirs(bare)
        git("init", "-q", "--bare", cwd=bare)
        git("config", "remote.up.url", f"https://{FAKE}@github.com/o/r.git", cwd=bare)
        n, _ = self.run_scan()
        self.assertEqual(n, 1)

    def test_fix_rewrites_token_free_and_is_idempotent(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, f"https://{FAKE}@github.com/o/r.git")
        n, out = self.run_scan(fix=True)
        self.assertEqual(n, 0)
        self.assertIn("FIXED", out)
        url = subprocess.run(["git", "config", "remote.lumenfork.url"], cwd=repo, capture_output=True, text=True).stdout.strip()
        self.assertEqual(url, "https://github.com/o/r.git")
        n2, out2 = self.run_scan(fix=True)
        self.assertEqual((n2, out2), (0, ""))

    def test_notify_carries_repo_path_not_url(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, f"https://{FAKE}@github.com/o/r.git")
        calls = []
        real_run = subprocess.run
        rcs.subprocess.run = lambda cmd, **kw: calls.append(cmd) or real_run(["true"])
        self.addCleanup(setattr, rcs.subprocess, "run", real_run)
        rcs.main(["scan", "--root", self.root, "--notify"])
        self.assertEqual(len(calls), 1)
        msg = " ".join(calls[0])
        self.assertIn(os.path.realpath(repo), msg)
        self.assertNotIn(FAKE, msg)

    def test_main_exit_codes(self):
        init_repo(os.path.join(self.root, "r"), f"https://{FAKE}@github.com/o/r.git")
        self.assertEqual(rcs.main(["scan", "--root", self.root]), 1)
        self.assertEqual(rcs.main(["scan", "--root", self.root, "--fix"]), 0)
        self.assertEqual(rcs.main(["scan", "--root", self.root]), 0)


if __name__ == "__main__":
    unittest.main()
