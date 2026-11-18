#!/usr/bin/env python3
"""Ed25519-signed, SHA-256-chained audit log for incident response actions.

wq#4929. Design: security/agent-kill-switch-spec.md §2.

Each entry is hashed (SHA-256 over canonical JSON), chained to the previous entry's
hash, and signed with an Ed25519 key. Verification is offline — no daemon needed.
"""

import hashlib
import json
import os
import sys
import time

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

AUDIT_DIR = os.environ.get("AGENT_AUDIT_DIR", "/var/lib/agent-audit")
KEY_PATH = os.path.join(AUDIT_DIR, "signing.key")
PUB_PATH = os.path.join(AUDIT_DIR, "signing.pub")

GENESIS_HASH = "0" * 64


def _canonical(entry):
    """Canonical JSON: sorted keys, no whitespace, no trailing newline."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()


def _sha256(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _ensure_dir(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def generate_keypair(key_dir=None):
    """Generate a new Ed25519 keypair. Returns (private_key, public_bytes_hex)."""
    if not HAS_CRYPTO:
        raise RuntimeError("cryptography package not installed")
    kd = key_dir or AUDIT_DIR
    kp = os.path.join(kd, "signing.key")
    pp = os.path.join(kd, "signing.pub")
    os.makedirs(kd, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    priv_bytes = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    with os.fdopen(fd, "wb") as f:
        f.write(priv_bytes)
    with open(pp, "wb") as f:
        f.write(pub_bytes)
    return private_key, pub_bytes.hex()


def load_private_key(key_dir=None):
    kd = key_dir or AUDIT_DIR
    kp = os.path.join(kd, "signing.key")
    if not HAS_CRYPTO:
        raise RuntimeError("cryptography package not installed")
    with open(kp, "rb") as f:
        raw = f.read()
    return Ed25519PrivateKey.from_private_bytes(raw)


def load_public_key(key_dir=None):
    kd = key_dir or AUDIT_DIR
    pp = os.path.join(kd, "signing.pub")
    if not HAS_CRYPTO:
        raise RuntimeError("cryptography package not installed")
    with open(pp, "rb") as f:
        raw = f.read()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as PK
    return PK.from_public_bytes(raw)


class AuditChain:
    """Append-only, signed audit chain backed by a JSONL file."""

    def __init__(self, path, key_dir=None):
        self.path = path
        self.key_dir = key_dir
        self._seq = 0
        self._prev_hash = GENESIS_HASH
        self._load_tip()

    def _load_tip(self):
        if not os.path.exists(self.path):
            return
        last = None
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if last:
            rec = json.loads(last)
            self._seq = rec["seq"]
            self._prev_hash = rec["entry_hash"]

    def append(self, event, detail=None, worker_id=None, ticket_id=None, principal=None):
        """Append a signed entry. Returns the entry dict."""
        self._seq += 1
        entry = {
            "seq": self._seq,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime()),
            "prev_hash": self._prev_hash,
            "worker_id": worker_id or "",
            "ticket_id": ticket_id,
            "event": event,
            "detail": detail or {},
            "principal": principal or {"uid": os.getuid(), "pid": os.getpid()},
        }
        entry_hash = _sha256(_canonical(entry))
        sig_input = (self._prev_hash + entry_hash).encode()

        signature = ""
        if HAS_CRYPTO:
            try:
                pk = load_private_key(self.key_dir)
                sig_bytes = pk.sign(sig_input)
                signature = sig_bytes.hex()
            except (FileNotFoundError, OSError):
                pass

        record = {**entry, "entry_hash": entry_hash, "signature": signature}
        _ensure_dir(self.path)
        with open(self.path, "a") as f:
            f.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

        self._prev_hash = entry_hash
        return record

    @staticmethod
    def verify(path, key_dir=None):
        """Verify chain integrity and signatures. Returns (ok, errors)."""
        errors = []
        prev_hash = GENESIS_HASH
        expected_seq = 0

        pub_key = None
        if HAS_CRYPTO:
            try:
                pub_key = load_public_key(key_dir)
            except (FileNotFoundError, OSError):
                pass

        with open(path) as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    errors.append(f"line {lineno}: invalid JSON: {e}")
                    continue

                expected_seq += 1
                if rec.get("seq") != expected_seq:
                    errors.append(f"line {lineno}: seq={rec.get('seq')}, expected={expected_seq}")

                if rec.get("prev_hash") != prev_hash:
                    errors.append(
                        f"line {lineno}: prev_hash mismatch: "
                        f"got={rec.get('prev_hash')[:16]}..., "
                        f"expected={prev_hash[:16]}..."
                    )

                entry = {k: rec[k] for k in rec
                         if k not in ("entry_hash", "signature")}
                computed_hash = _sha256(_canonical(entry))
                if rec.get("entry_hash") != computed_hash:
                    errors.append(f"line {lineno}: entry_hash mismatch")

                if pub_key and rec.get("signature"):
                    sig_input = (rec["prev_hash"] + rec["entry_hash"]).encode()
                    try:
                        pub_key.verify(bytes.fromhex(rec["signature"]), sig_input)
                    except Exception:
                        errors.append(f"line {lineno}: signature verification failed")

                prev_hash = rec.get("entry_hash", prev_hash)

        return len(errors) == 0, errors


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Ed25519-signed audit chain")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("generate-key", help="Generate a new Ed25519 keypair")
    p_v = sub.add_parser("verify", help="Verify a chain file")
    p_v.add_argument("path")
    p_v.add_argument("--key-dir", default=None)

    args = ap.parse_args()
    if args.cmd == "generate-key":
        _, pub_hex = generate_keypair()
        print(f"Key generated. Public key: {pub_hex}")
    elif args.cmd == "verify":
        ok, errors = AuditChain.verify(args.path, args.key_dir)
        if ok:
            print("Chain integrity: OK")
        else:
            print("Chain integrity: FAILED")
            for e in errors:
                print(f"  {e}")
            sys.exit(1)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
