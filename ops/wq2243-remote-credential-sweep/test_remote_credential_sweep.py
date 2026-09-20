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

    def capture_pages(self):
        pages = []
        real_run = subprocess.run

        def fake(cmd, **kw):
            if cmd[0] == rcs.NTFY_BIN:
                pages.append(" ".join(cmd))
                return real_run(["true"])
            return real_run(cmd, **kw)

        rcs.subprocess.run = fake
        self.addCleanup(setattr, rcs.subprocess, "run", real_run)
        return pages

    def append_config(self, repo, text):
        with open(os.path.join(repo, ".git", "config"), "a", encoding="utf-8") as f:
            f.write(text)

    def git_config(self, repo, *args):
        return subprocess.run(["git", "config", *args], cwd=repo, capture_output=True, text=True).stdout.strip()

    def test_notify_pages_on_fixed_url(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, f"https://{FAKE}@github.com/o/r.git")
        pages = self.capture_pages()
        self.assertEqual(rcs.main(["scan", "--root", self.root, "--fix", "--notify"]), 0)
        self.assertEqual(len(pages), 1)
        self.assertIn(os.path.realpath(repo), pages[0])
        self.assertIn("stripped", pages[0])
        self.assertNotIn(FAKE, pages[0])
        self.assertEqual(self.git_config(repo, "remote.lumenfork.url"), "https://github.com/o/r.git")
        # the token is gone from the config, so the next run is quiet
        self.assertEqual(rcs.main(["scan", "--root", self.root, "--fix", "--notify"]), 0)
        self.assertEqual(len(pages), 1)

    def test_notify_mixed_fixed_and_embedded_pages_once(self):
        init_repo(os.path.join(self.root, "a"), f"https://{FAKE}@github.com/o/r.git")
        init_repo(os.path.join(self.root, "b"), f"https://github.com/o/{FAKE}/r.git")
        pages = self.capture_pages()
        self.assertEqual(rcs.main(["scan", "--root", self.root, "--fix", "--notify"]), 1)
        self.assertEqual(len(pages), 1)
        self.assertIn("stripped", pages[0])
        self.assertIn("credential in git remote URL", pages[0])

    def test_raw_at_in_password_leaves_no_fragment(self):
        self.assertEqual(rcs.strip_userinfo("https://user:pa@ss@host/o/r"), "https://host/o/r")
        self.assertEqual(rcs.strip_userinfo("https://user:pa@ss@host"), "https://host")
        self.assertIsNone(rcs.strip_userinfo("https://host/o/r@v1"))
        self.assertIsNone(rcs.strip_userinfo("https://@host/o/r"))
        repo = os.path.join(self.root, "r")
        init_repo(repo, "https://user:pa@ss@host/o/r")
        n, _ = self.run_scan(fix=True)
        self.assertEqual(n, 0)
        self.assertEqual(self.git_config(repo, "remote.lumenfork.url"), "https://host/o/r")

    def test_token_in_query_or_fragment_is_stripped(self):
        self.assertEqual(rcs.strip_credentials(f"https://github.com/o/r.git?t={FAKE}&x=1"), "https://github.com/o/r.git?x=1")
        self.assertEqual(rcs.strip_credentials(f"https://github.com/o/r.git?t={FAKE}"), "https://github.com/o/r.git")
        self.assertEqual(rcs.strip_credentials(f"https://github.com/o/r.git#{FAKE}"), "https://github.com/o/r.git")
        repo = os.path.join(self.root, "r")
        init_repo(repo, f"https://github.com/o/r.git?t={FAKE}")
        n, out = self.run_scan(fix=True)
        self.assertEqual((n, out.count("FIXED")), (0, 1))
        self.assertEqual(self.git_config(repo, "remote.lumenfork.url"), "https://github.com/o/r.git")

    def test_token_in_path_is_reported_distinctly_and_left_alone(self):
        url = f"https://github.com/{FAKE}/r.git"
        self.assertFalse(rcs.strippable(url))
        repo = os.path.join(self.root, "r")
        init_repo(repo, url)
        n, out = self.run_scan(fix=True)
        self.assertEqual(n, 1)
        self.assertIn("UNSTRIPPABLE", out)
        self.assertNotIn("FIXED", out)
        self.assertNotIn(FAKE, out)
        self.assertEqual(self.git_config(repo, "remote.lumenfork.url"), url)

    def test_same_line_remote_section_parsed(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, "https://github.com/o/r.git")
        self.append_config(repo, f'[remote "oneline"] url = https://{FAKE}@github.com/o/r.git\n')
        n, out = self.run_scan()
        self.assertEqual(n, 1)
        self.assertIn("remote=oneline key=url", out)
        self.assertNotIn(FAKE, out)
        n, _ = self.run_scan(fix=True)
        self.assertEqual(n, 0)
        self.assertEqual(self.git_config(repo, "remote.oneline.url"), "https://github.com/o/r.git")

    def test_url_insteadof_section_with_token_found_and_fixed(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, "https://github.com/o/r.git")
        self.git_config(repo, f"url.https://{FAKE}@github.com/.insteadOf", "gh:")
        self.git_config(repo, "url.https://github.com/.pushInsteadOf", "ghp:")
        n, out = self.run_scan()
        self.assertEqual(n, 1)
        self.assertIn("remote=url#1 key=url-section", out)
        self.assertNotIn(FAKE, out)
        n, out = self.run_scan(fix=True)
        self.assertEqual(n, 0)
        self.assertEqual(out.count("FIXED"), 1)
        rules = self.git_config(repo, "--get-regexp", "^url\\..*insteadof$")
        self.assertNotIn(FAKE, rules)
        self.assertIn("url.https://github.com/.insteadof gh:", rules)
        self.assertEqual(self.run_scan(fix=True), (0, ""))

    def test_same_line_url_section_fixed(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, "https://github.com/o/r.git")
        self.append_config(repo, f'[url "https://u:{OPAQUE}@host/"] insteadOf = h:\n')
        mode = os.stat(os.path.join(repo, ".git", "config")).st_mode
        n, _ = self.run_scan(fix=True)
        self.assertEqual(n, 0)
        self.assertEqual(self.git_config(repo, "--get", "url.https://host/.insteadof"), "h:")
        self.assertEqual(os.stat(os.path.join(repo, ".git", "config")).st_mode, mode)

    def test_main_exit_codes(self):
        init_repo(os.path.join(self.root, "r"), f"https://{FAKE}@github.com/o/r.git")
        self.assertEqual(rcs.main(["scan", "--root", self.root]), 1)
        self.assertEqual(rcs.main(["scan", "--root", self.root, "--fix"]), 0)
        self.assertEqual(rcs.main(["scan", "--root", self.root]), 0)


if __name__ == "__main__":
    unittest.main()
