import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import add_access_hostname as m  # noqa: E402


APP_SELF_HOSTED = {
    "id": "app-1",
    "name": "home-front",
    "self_hosted_domains": ["home.lumennode.uk", "vault.lumennode.uk"],
}
APP_LEGACY_DOMAIN = {
    "id": "app-2",
    "name": "other-front",
    "domain": "other.lumennode.uk",
}


class DomainsTest(unittest.TestCase):
    def test_self_hosted_domains(self):
        self.assertEqual(
            m._domains(APP_SELF_HOSTED),
            ["home.lumennode.uk", "vault.lumennode.uk"])

    def test_legacy_domain_field(self):
        self.assertEqual(m._domains(APP_LEGACY_DOMAIN), ["other.lumennode.uk"])

    def test_legacy_domain_not_duplicated(self):
        app = {"self_hosted_domains": ["a.example"], "domain": "a.example"}
        self.assertEqual(m._domains(app), ["a.example"])


class FindFrontAppTest(unittest.TestCase):
    def test_single_match(self):
        calls = []

        def fake_get(url, token):
            calls.append(url)
            return {"success": True, "result": [APP_SELF_HOSTED, APP_LEGACY_DOMAIN]}

        orig = m._cf_get
        m._cf_get = fake_get
        try:
            app = m.find_front_app("acct", "tok", "home.lumennode.uk")
        finally:
            m._cf_get = orig
        self.assertEqual(app["id"], "app-1")
        self.assertEqual(len(calls), 1)

    def test_no_match_raises(self):
        m._cf_get = lambda url, token: {"success": True, "result": [APP_LEGACY_DOMAIN]}
        with self.assertRaisesRegex(RuntimeError, "no Access app"):
            m.find_front_app("acct", "tok", "home.lumennode.uk")

    def test_multiple_matches_refuses(self):
        dup = dict(APP_SELF_HOSTED, id="app-3", name="dup-front")
        m._cf_get = lambda url, token: {"success": True, "result": [APP_SELF_HOSTED, dup]}
        with self.assertRaisesRegex(RuntimeError, "refusing to guess"):
            m.find_front_app("acct", "tok", "home.lumennode.uk")

    def test_api_error_raises(self):
        m._cf_get = lambda url, token: {"success": False, "errors": ["bad token"]}
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            m.find_front_app("acct", "tok", "home.lumennode.uk")


class AddHostnameTest(unittest.TestCase):
    def test_already_present_is_noop(self):
        put_calls = []
        m._cf_put = lambda *a, **k: put_calls.append((a, k)) or {}
        m.add_hostname("acct", "tok", APP_SELF_HOSTED, "home.lumennode.uk", dry_run=False)
        self.assertEqual(put_calls, [])

    def test_dry_run_does_not_put(self):
        put_calls = []
        m._cf_put = lambda *a, **k: put_calls.append((a, k)) or {}
        m.add_hostname("acct", "tok", APP_SELF_HOSTED, "prompts.lumennode.uk", dry_run=True)
        self.assertEqual(put_calls, [])

    def test_live_put_appends_and_preserves_existing(self):
        captured = {}

        def fake_put(url, token, body):
            captured["url"] = url
            captured["body"] = body
            return {"success": True, "result": {}}

        orig = m._cf_put
        m._cf_put = fake_put
        try:
            m.add_hostname("acct", "tok", APP_SELF_HOSTED, "prompts.lumennode.uk", dry_run=False)
        finally:
            m._cf_put = orig
        self.assertIn("app-1", captured["url"])
        self.assertEqual(
            captured["body"]["self_hosted_domains"],
            ["home.lumennode.uk", "vault.lumennode.uk", "prompts.lumennode.uk"])

    def test_live_put_failure_raises(self):
        m._cf_put = lambda url, token, body: {"success": False, "errors": ["nope"]}
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            m.add_hostname("acct", "tok", APP_SELF_HOSTED, "prompts.lumennode.uk", dry_run=False)


class MainTest(unittest.TestCase):
    """main() — the CLI entry point and its exit-code contract — had zero
    coverage: everything below it (find_front_app/add_hostname) was tested
    directly, but argument parsing, config-file reads, and which exception
    maps to which exit code were never exercised."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.token_file = os.path.join(self.tmpdir.name, "token")
        self.account_file = os.path.join(self.tmpdir.name, "account")
        with open(self.token_file, "w") as f:
            f.write("tok\n")
        with open(self.account_file, "w") as f:
            f.write("acct\n")
        self.addCleanup(setattr, m, "_cf_get", m._cf_get)
        self.addCleanup(setattr, m, "_cf_put", m._cf_put)

    def _argv(self, **extra):
        argv = ["--add-hostname", "prompts.lumennode.uk",
                "--token-file", self.token_file,
                "--account-id-file", self.account_file]
        for k, v in extra.items():
            argv += [f"--{k.replace('_', '-')}", v]
        return argv

    def test_missing_token_file_is_usage_error(self):
        rc = m.main(["--add-hostname", "prompts.lumennode.uk",
                     "--token-file", os.path.join(self.tmpdir.name, "nope"),
                     "--account-id-file", self.account_file])
        self.assertEqual(rc, m.EXIT_USAGE)

    def test_missing_account_file_is_usage_error(self):
        rc = m.main(["--add-hostname", "prompts.lumennode.uk",
                     "--token-file", self.token_file,
                     "--account-id-file", os.path.join(self.tmpdir.name, "nope")])
        self.assertEqual(rc, m.EXIT_USAGE)

    def test_lookup_failure_is_exit_failed(self):
        m._cf_get = lambda url, token: {"success": False, "errors": ["bad token"]}
        rc = m.main(self._argv())
        self.assertEqual(rc, m.EXIT_FAILED)

    def test_ambiguous_match_is_exit_failed(self):
        dup = dict(APP_SELF_HOSTED, id="app-3")
        m._cf_get = lambda url, token: {"success": True, "result": [APP_SELF_HOSTED, dup]}
        rc = m.main(self._argv())
        self.assertEqual(rc, m.EXIT_FAILED)

    def test_success_path_is_exit_ok(self):
        m._cf_get = lambda url, token: {"success": True, "result": [APP_SELF_HOSTED]}
        m._cf_put = lambda url, token, body: {"success": True, "result": {}}
        rc = m.main(self._argv())
        self.assertEqual(rc, m.EXIT_OK)

    def test_dry_run_never_calls_put(self):
        put_calls = []
        m._cf_get = lambda url, token: {"success": True, "result": [APP_SELF_HOSTED]}
        m._cf_put = lambda *a, **k: put_calls.append((a, k)) or {"success": True}
        rc = m.main(self._argv() + ["--dry-run"])
        self.assertEqual(rc, m.EXIT_OK)
        self.assertEqual(put_calls, [])


if __name__ == "__main__":
    unittest.main()
