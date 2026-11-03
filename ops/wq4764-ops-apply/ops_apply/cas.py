"""Content-addressable store for staged apply artifacts (wq#4829, aerynOS
CAS pattern: stored by content hash, composed as part of a transaction,
never modified in place).

Layout (ticket wq#4829):
  <cas_root>/<sha256[:2]>/<sha256>          blob, written atomically, 0o444
  <cas_root>/manifests/wq<ticket>.json      ticket -> [{name, digest, ...}]

The blob path IS the claim: a file at `ab/abcd...` claims to hash to
`abcd...`. `verify()` re-hashes the whole blob and refuses (OPS_CAS) on any
mismatch, so the approved digest — carried inside the signed manifest — is
re-checked against actual bytes at apply time. That closes the
approval->apply TOCTOU: approving a digest approves exactly one byte
sequence, and nothing that happens to the filesystem afterwards can swap
the content without flipping the hash.

Digest strings use the same `sha256:<hex>` form as canonical.manifest_digest.
"""
import fcntl
import json
import os
import time

from .canonical import sha256_hex
from .errors import OpsApplyError

DEFAULT_CAS_ROOT = "/home/lumen/.cas"
_HEX = "0123456789abcdef"


def _hex_or_raise(digest):
    """`sha256:<64 hex>` -> the hex part, OPS_CAS on anything else."""
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise OpsApplyError("OPS_CAS", f"malformed digest: {digest!r}")
    hexpart = digest[len("sha256:"):]
    if len(hexpart) != 64 or any(c not in _HEX for c in hexpart):
        raise OpsApplyError("OPS_CAS", f"malformed digest: {digest!r}")
    return hexpart


def blob_path(cas_root, digest):
    hexpart = _hex_or_raise(digest)
    return os.path.join(cas_root, hexpart[:2], hexpart)


def store(cas_root, content):
    """Write `content` (bytes or str) into the CAS; returns its
    `sha256:<hex>` digest. Idempotent: an existing blob is verified rather
    than rewritten (never modified in place), so storing the same artifact
    for ten tickets costs one file. Atomic: tmp in the same directory +
    os.replace, so a reader never sees a half-written blob."""
    if isinstance(content, str):
        content = content.encode()
    digest = "sha256:" + sha256_hex(content)
    path = blob_path(cas_root, digest)
    if os.path.exists(path):
        verify(cas_root, digest)
        return digest
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return digest


def verify(cas_root, digest):
    """Re-hash the blob for `digest`; return its path, or raise OPS_CAS if
    it is missing or its bytes no longer hash to the digest. This is the
    apply-time gate: a refusal here means the artifact changed after it was
    approved."""
    path = blob_path(cas_root, digest)
    hexpart = digest[len("sha256:"):]
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError as e:
        raise OpsApplyError("OPS_CAS", f"artifact {digest} not in CAS: {e}") from e
    actual = sha256_hex(content)
    if actual != hexpart:
        raise OpsApplyError(
            "OPS_CAS",
            f"artifact tampered: stored as {digest} but hashes to sha256:{actual}")
    return path


def retrieve(cas_root, digest):
    """Verified read: the bytes for `digest`, re-hashed on every read."""
    path = verify(cas_root, digest)
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# ticket manifests — the audit trail is a list of digests, not paths
# ---------------------------------------------------------------------------

def _manifest_path(cas_root, ticket):
    return os.path.join(cas_root, "manifests", f"wq{ticket}.json")


def _mutate_manifest(cas_root, ticket, fn):
    """flock + whole-file atomic rewrite, same convention as queue.py, so
    two racing requests on one ticket never interleave a read-modify-write."""
    path = _manifest_path(cas_root, ticket)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, "r+") as f:
            fd = None
            raw = f.read()
            doc = json.loads(raw) if raw else {"ticket": ticket, "artifacts": []}
            doc = fn(doc)
            f.seek(0)
            f.truncate()
            json.dump(doc, f, sort_keys=True, indent=1)
            f.flush()
            os.fsync(f.fileno())
            return doc
    finally:
        if fd is not None:
            os.close(fd)


def record_artifact(cas_root, ticket, name, digest, *, req=None, metadata=None,
                    now=None):
    """Append (or update) the ticket-manifest entry mapping `name` to
    `digest`. One entry per (name, digest) pair; re-staging the same
    artifact refreshes its metadata instead of duplicating the row."""
    _hex_or_raise(digest)
    now = time.time() if now is None else now

    def _mutate(doc):
        for entry in doc["artifacts"]:
            if entry["name"] == name and entry["digest"] == digest:
                entry["stored_at"] = now
                if req is not None:
                    entry["req"] = req
                if metadata is not None:
                    entry["metadata"] = metadata
                return doc
        doc["artifacts"].append({
            "name": name, "digest": digest, "stored_at": now,
            "req": req, "metadata": metadata, "approval": None,
        })
        return doc

    return _mutate_manifest(cas_root, ticket, _mutate)


def record_approval(cas_root, ticket, digest, approval):
    """Stamp the approval record (who/when) onto every entry for `digest` —
    the ticket manifest then carries artifact digest + metadata + approval,
    per the wq#4829 layout."""

    def _mutate(doc):
        for entry in doc["artifacts"]:
            if entry["digest"] == digest:
                entry["approval"] = approval
        return doc

    return _mutate_manifest(cas_root, ticket, _mutate)


def load_ticket_manifest(cas_root, ticket):
    path = _manifest_path(cas_root, ticket)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)
