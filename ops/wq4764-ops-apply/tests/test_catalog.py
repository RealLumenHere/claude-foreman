"""catalog.json pins two sha256es (its own recipe file, and the policy
copy). If either source file changes without the catalog being
regenerated, this must fail loud (V6/V8's whole point) rather than let a
stale pin silently keep passing."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ops_apply.canonical import file_sha256


class CatalogPinTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "catalog.json")) as f:
            self.catalog = json.load(f)

    def test_policy_sha_matches_pinned_copy(self):
        actual = file_sha256(os.path.join(ROOT, "policy.v1.yaml"))
        self.assertEqual(self.catalog["policy_sha256"], actual)

    def test_recipe_sha_matches_file_on_disk(self):
        entry = self.catalog["recipes"][0]
        self.assertEqual(entry["id"], "cloudflared.ingress.upsert")
        path = os.path.join(ROOT, "ops_apply", "recipes", "cloudflared_ingress_upsert.py")
        self.assertEqual(entry["sha256"], file_sha256(path))

    def test_param_schema_forbids_additional_properties(self):
        entry = self.catalog["recipes"][0]
        self.assertIs(entry["param_schema"]["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
