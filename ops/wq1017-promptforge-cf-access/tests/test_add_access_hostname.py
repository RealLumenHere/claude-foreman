import os
import sys
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


if __name__ == "__main__":
    unittest.main()
