"""Canonical JSON + digest (design §2.6 convention, reused from
harness/hold_gate.py:canonical_input/fingerprint): sort_keys + compact
separators, sha256 hex digest. The manifest digest is computed over every
field except `digest` itself.
"""
import hashlib
import json


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_hex(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def manifest_digest(manifest):
    """sha256 of the canonical JSON of `manifest` with the `digest` key
    removed, whatever it was set to. Matches design §3.3's
    `digest = sha256(canonical_json(manifest minus digest))`."""
    body = {k: v for k, v in manifest.items() if k != "digest"}
    return "sha256:" + sha256_hex(canonical_json(body))


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
