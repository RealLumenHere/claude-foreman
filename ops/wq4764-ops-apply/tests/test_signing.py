import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_apply import signing


class FakeCompleted:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


class SignDigestTests(unittest.TestCase):
    def test_sign_writes_sig_alongside_message_and_returns_its_contents(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            msg_path = argv[-1]
            with open(f"{msg_path}.sig", "w") as f:
                f.write("-----BEGIN SSH SIGNATURE-----\nfake\n-----END SSH SIGNATURE-----\n")
            return FakeCompleted(0)

        sig = signing.sign_digest("sha256:abc", "/etc/ops-apply/signing_key", runner=runner)
        self.assertIn("SSH SIGNATURE", sig)
        self.assertEqual(calls[0][:4], ["ssh-keygen", "-Y", "sign", "-f"])
        self.assertEqual(calls[0][4], "/etc/ops-apply/signing_key")

    def test_sign_raises_on_nonzero_exit(self):
        def runner(argv, **kwargs):
            return FakeCompleted(1, "boom")

        with self.assertRaises(RuntimeError):
            signing.sign_digest("sha256:abc", "/etc/ops-apply/signing_key", runner=runner)


class VerifyDigestTests(unittest.TestCase):
    def test_verify_true_on_zero_exit(self):
        def runner(argv, **kwargs):
            self.assertEqual(kwargs.get("input"), "sha256:abc")
            return FakeCompleted(0)

        self.assertTrue(signing.verify_digest(
            digest="sha256:abc", signature="sig-text",
            allowed_signers_path="/etc/ops-apply/allowed_signers", runner=runner))

    def test_verify_false_on_nonzero_exit_never_raises(self):
        def runner(argv, **kwargs):
            return FakeCompleted(1)

        self.assertFalse(signing.verify_digest(
            digest="sha256:abc", signature="bad-sig",
            allowed_signers_path="/etc/ops-apply/allowed_signers", runner=runner))

    def test_verify_argv_shape(self):
        seen = {}

        def runner(argv, **kwargs):
            seen["argv"] = argv
            return FakeCompleted(0)

        signing.verify_digest(digest="d", signature="s",
                              allowed_signers_path="/etc/ops-apply/allowed_signers", runner=runner)
        argv = seen["argv"]
        self.assertEqual(argv[:3], ["ssh-keygen", "-Y", "verify"])
        self.assertIn("-f", argv)
        self.assertIn("/etc/ops-apply/allowed_signers", argv)
        self.assertIn("-I", argv)
        self.assertIn("-n", argv)
        self.assertIn(signing.NAMESPACE, argv)


if __name__ == "__main__":
    unittest.main()
