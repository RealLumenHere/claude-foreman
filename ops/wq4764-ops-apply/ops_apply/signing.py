"""V8 signature: `ssh-keygen -Y sign`/`-Y verify` over the manifest digest
(design §3.4 V8). Confirmed invocation shape from `man ssh-keygen` (fetched
2026-10-28, this host):

    ssh-keygen -Y sign -f key_file -n namespace file
        -> writes signature to "<file>.sig" (zero exit on success)
    ssh-keygen -Y verify -f allowed_signers_file -I signer_identity \
        -n namespace -s signature_file  (message on stdin)
        -> zero exit means the signer in allowed_signers_file signed it

The signing key is root-only 0600 in /etc/ops-apply/ (design §3.4 V8: "uid
lumen has no OS read path to it"), so only the gate process can ever call
sign_digest(); the node only ever calls verify_digest().
"""
import subprocess
import tempfile

NAMESPACE = "ops-apply@lumen"


def sign_digest(digest, key_path, *, namespace=NAMESPACE, runner=subprocess.run):
    """Returns the signature text (the contents of "<tmp>.sig")."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".msg") as msg:
        msg.write(digest)
        msg.flush()
        result = runner(
            ["ssh-keygen", "-Y", "sign", "-f", key_path, "-n", namespace, msg.name],
            capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ssh-keygen -Y sign failed: {result.stderr}")
        with open(f"{msg.name}.sig") as f:
            return f.read()


def verify_digest(*, digest, signature, allowed_signers_path, signer_identity="ops-apply",
                   namespace=NAMESPACE, runner=subprocess.run):
    """True iff `signature` is a valid ssh-keygen -Y signature over `digest`
    by a signer listed in `allowed_signers_path`. Never raises on a bad
    signature — that is an V8 refusal, not an exception."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sig") as sig_f:
        sig_f.write(signature)
        sig_f.flush()
        result = runner(
            ["ssh-keygen", "-Y", "verify", "-f", allowed_signers_path,
             "-I", signer_identity, "-n", namespace, "-s", sig_f.name],
            input=digest, capture_output=True, text=True, check=False)
        return result.returncode == 0
