import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_apply import canonical


class CanonicalJsonTests(unittest.TestCase):
    def test_key_order_independent(self):
        a = canonical.canonical_json({"b": 1, "a": 2})
        b = canonical.canonical_json({"a": 2, "b": 1})
        self.assertEqual(a, b)

    def test_compact_separators(self):
        self.assertEqual(canonical.canonical_json({"a": 1}), '{"a":1}')


class ManifestDigestTests(unittest.TestCase):
    def test_digest_excludes_digest_field(self):
        manifest = {"recipe": "x", "ticket": 1}
        with_digest = dict(manifest, digest="whatever-stale-value")
        self.assertEqual(canonical.manifest_digest(manifest),
                         canonical.manifest_digest(with_digest))

    def test_digest_changes_with_params(self):
        d1 = canonical.manifest_digest({"recipe": "x", "params": {"port": 1}})
        d2 = canonical.manifest_digest({"recipe": "x", "params": {"port": 2}})
        self.assertNotEqual(d1, d2)

    def test_digest_is_prefixed_sha256(self):
        d = canonical.manifest_digest({"a": 1})
        self.assertTrue(d.startswith("sha256:"))
        self.assertEqual(len(d), len("sha256:") + 64)


class FileSha256Tests(unittest.TestCase):
    def test_matches_known_content(self):
        import hashlib
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            path = f.name
        try:
            expected = hashlib.sha256(b"hello world").hexdigest()
            self.assertEqual(canonical.file_sha256(path), expected)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
