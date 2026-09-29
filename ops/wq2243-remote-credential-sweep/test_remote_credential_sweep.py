import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import remote_credential_sweep as rcs

# Built at runtime: a token-shaped literal in the source gets replaced by the wq523 scrubber on commit
# (this line read "<REDACTED-wq523 ...>" and 9 tests failed). 36 body chars, never a real credential.
FAKE = "gh" + "p_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5"
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

    def test_prefix_inside_repo_name_is_not_a_token(self):
        for u in ("https://github.com/org/highs_solver.git", "https://github.com/org/ghp_notes.git",
                  "https://github.com/org/xghp_" + "A" * 36 + ".git", "https://github.com/org/ghs_x.git"):
            self.assertFalse(rcs.has_credential(u), u)

    def test_real_token_shapes_still_flagged_at_a_boundary(self):
        for tok in (FAKE, "gho_" + "A" * 36, "github_pat_" + "A1_" * 20, "glpat-" + "a-b_" * 6):
            self.assertTrue(rcs.has_credential(f"https://github.com/o/{tok}/r.git"), tok)
            self.assertTrue(rcs.has_credential(f"https://github.com/o/r.git?t={tok}"), tok)

    def test_parse_value_decodes_git_quoting(self):
        self.assertEqual(rcs._parse_value('"https://u:pw#1@h/"'), "https://u:pw#1@h/")
        self.assertEqual(rcs._parse_value(r'"https://u:p\"w\\1@h/;x"'), 'https://u:p"w\\1@h/;x')
        self.assertEqual(rcs._parse_value("https://h/o/r   # note"), "https://h/o/r")
        self.assertEqual(rcs._parse_value('https://h/o/r ; note'), "https://h/o/r")
        self.assertEqual(rcs._parse_value('"a " '), "a ")

    def test_encode_value_round_trips(self):
        for v in ("https://h/o/r", "https://h/o/r#frag", "https://h/o;r", 'https://h/"q"\\', " lead"):
            self.assertEqual(rcs._parse_value(rcs._encode_value(v)), v)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self._state = tempfile.TemporaryDirectory()
        self.addCleanup(self._state.cleanup)
        patched = os.path.join(self._state.name, "paged")
        self.addCleanup(setattr, rcs, "DEFAULT_STATE", rcs.DEFAULT_STATE)
        rcs.DEFAULT_STATE = patched

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

    def test_quoted_url_value_flagged_and_fixed(self):
        for pw in ("pw#1", "pw;1"):
            with self.subTest(pw=pw):
                repo = os.path.join(self.root, "r" + str(abs(hash(pw))))
                init_repo(repo, f"https://user:{pw}@host/o/r")
                with open(os.path.join(repo, ".git", "config"), encoding="utf-8") as f:
                    self.assertIn('url = "https://user:', f.read())  # git quoted it
                n, out = self.run_scan()
                self.assertEqual((n, out.count("EMBEDDED")), (1, 1))
                self.assertNotIn(pw, out)
                self.assertEqual(self.run_scan(fix=True)[0], 0)
                self.assertEqual(self.git_config(repo, "remote.lumenfork.url"), "https://host/o/r")
                self.assertEqual(self.run_scan(), (0, ""))
                shutil.rmtree(repo)

    def test_quoted_clean_url_with_fragment_left_alone(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, "https://host/o/r#frag")
        self.assertEqual(self.run_scan(fix=True), (0, ""))
        self.assertEqual(self.git_config(repo, "remote.lumenfork.url"), "https://host/o/r#frag")

    def test_fix_keeps_quoting_when_clean_url_needs_it(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, f"https://user:pw@host/o/r#frag")
        self.assertEqual(self.run_scan(fix=True)[0], 0)
        self.assertEqual(self.git_config(repo, "remote.lumenfork.url"), "https://host/o/r#frag")

    def test_repo_named_like_a_token_prefix_is_not_paged(self):
        init_repo(os.path.join(self.root, "a"), "https://github.com/org/highs_solver.git")
        init_repo(os.path.join(self.root, "b"), "https://github.com/org/ghp_notes.git", name="n")
        pages = self.capture_pages()
        self.assertEqual(rcs.main(["scan", "--root", self.root, "--fix", "--notify"]), 0)
        self.assertEqual(pages, [])

    def test_unstrippable_pages_once_then_stays_quiet(self):
        a = os.path.join(self.root, "a")
        init_repo(a, f"https://github.com/o/{FAKE}/r.git")
        pages = self.capture_pages()
        args = ["scan", "--root", self.root, "--fix", "--notify"]
        self.assertEqual(rcs.main(args), 1)  # still a leak: exit stays 1
        self.assertEqual(len(pages), 1)
        self.assertIn("cannot auto-strip", pages[0])
        self.assertIn(os.path.realpath(a), pages[0])
        self.assertNotIn(FAKE, pages[0])
        self.assertEqual(rcs.main(args), 1)
        self.assertEqual(len(pages), 1)
        # a second stuck repo pages, naming only itself
        b = os.path.join(self.root, "b")
        init_repo(b, f"https://github.com/o/{FAKE}/r.git")
        self.assertEqual(rcs.main(args), 1)
        self.assertEqual(len(pages), 2)
        self.assertIn(os.path.realpath(b), pages[1])
        self.assertNotIn(os.path.realpath(a), pages[1])

    def test_unstrippable_pages_again_after_it_clears_and_recurs(self):
        repo = os.path.join(self.root, "a")
        init_repo(repo, f"https://github.com/o/{FAKE}/r.git")
        pages = self.capture_pages()
        args = ["scan", "--root", self.root, "--notify"]
        rcs.main(args)
        self.git_config(repo, "remote.lumenfork.url", "https://github.com/o/r.git")
        self.assertEqual(rcs.main(args), 0)
        self.git_config(repo, "remote.lumenfork.url", f"https://github.com/o/{FAKE}/r.git")
        rcs.main(args)
        self.assertEqual(len(pages), 2)

    def test_unstrippable_page_failure_is_retried(self):
        init_repo(os.path.join(self.root, "a"), f"https://github.com/o/{FAKE}/r.git")
        real_run = subprocess.run
        rcs.subprocess.run = lambda cmd, **kw: real_run(["false"])
        self.addCleanup(setattr, rcs.subprocess, "run", real_run)
        rcs.main(["scan", "--root", self.root, "--notify"])
        rcs.subprocess.run = real_run
        pages = self.capture_pages()
        rcs.main(["scan", "--root", self.root, "--notify"])
        self.assertEqual(len(pages), 1)

    def test_fixed_and_embedded_still_page_every_run_but_stuck_only_once(self):
        init_repo(os.path.join(self.root, "a"), f"https://github.com/o/{FAKE}/r.git")
        init_repo(os.path.join(self.root, "b"), f"https://{FAKE}@github.com/o/r.git")
        pages = self.capture_pages()
        args = ["scan", "--root", self.root, "--fix", "--notify"]
        rcs.main(args)
        rcs.main(args)
        self.assertEqual(len(pages), 1)  # b fixed on run 1; a paged on run 1 only
        self.assertIn("stripped", pages[0])
        self.assertIn("cannot auto-strip", pages[0])

    def test_repeated_url_entries_each_fixed(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, f"https://{FAKE}@github.com/o/r.git")
        self.git_config(repo, "--add", "remote.lumenfork.url", f"https://u:{OPAQUE}@host/o/r")
        n, out = self.run_scan()
        self.assertEqual((n, out.count("EMBEDDED")), (2, 2))
        n, out = self.run_scan(fix=True)
        self.assertEqual((n, out.count("FIXED")), (0, 2))
        self.assertEqual(self.git_config(repo, "--get-all", "remote.lumenfork.url").splitlines(),
                         ["https://github.com/o/r.git", "https://host/o/r"])
        self.assertEqual(self.run_scan(), (0, ""))

    def test_repeated_entry_fix_leaves_clean_sibling_alone(self):
        repo = os.path.join(self.root, "r")
        init_repo(repo, "https://github.com/o/clean.git", key="pushurl")
        self.git_config(repo, "--add", "remote.lumenfork.pushurl", f"https://{FAKE}@github.com/o/r.git")
        self.git_config(repo, "--add", "remote.lumenfork.pushurl", "https://github.com/o/also-clean.git")
        self.assertEqual(self.run_scan(fix=True)[0], 0)
        self.assertEqual(self.git_config(repo, "--get-all", "remote.lumenfork.pushurl").splitlines(),
                         ["https://github.com/o/clean.git", "https://github.com/o/r.git",
                          "https://github.com/o/also-clean.git"])

    def test_main_exit_codes(self):
        init_repo(os.path.join(self.root, "r"), f"https://{FAKE}@github.com/o/r.git")
        self.assertEqual(rcs.main(["scan", "--root", self.root]), 1)
        self.assertEqual(rcs.main(["scan", "--root", self.root, "--fix"]), 0)
        self.assertEqual(rcs.main(["scan", "--root", self.root]), 0)


if __name__ == "__main__":
    unittest.main()
