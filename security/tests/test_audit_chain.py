#!/usr/bin/env python3
"""Tests for the Ed25519-signed audit chain (wq#4929)."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from audit_chain import AuditChain, generate_keypair, GENESIS_HASH, _sha256, _canonical, HAS_CRYPTO


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def chain_path(tmp_dir):
    return os.path.join(tmp_dir, "test-chain.jsonl")


@pytest.fixture
def key_dir(tmp_dir):
    if not HAS_CRYPTO:
        pytest.skip("cryptography package not installed")
    kd = os.path.join(tmp_dir, "keys")
    generate_keypair(kd)
    return kd


class TestAuditChainUnsigned:
    """Chain integrity without signing (works without cryptography package)."""

    def test_empty_chain_starts_at_genesis(self, chain_path):
        c = AuditChain(chain_path)
        assert c._seq == 0
        assert c._prev_hash == GENESIS_HASH

    def test_append_creates_file(self, chain_path):
        c = AuditChain(chain_path)
        rec = c.append("test_event", detail={"foo": "bar"}, ticket_id=1)
        assert os.path.exists(chain_path)
        assert rec["seq"] == 1
        assert rec["prev_hash"] == GENESIS_HASH
        assert rec["event"] == "test_event"
        assert rec["detail"] == {"foo": "bar"}

    def test_chain_links(self, chain_path):
        c = AuditChain(chain_path)
        r1 = c.append("first", ticket_id=1)
        r2 = c.append("second", ticket_id=1)
        assert r2["prev_hash"] == r1["entry_hash"]
        assert r2["seq"] == 2

    def test_three_entry_chain(self, chain_path):
        c = AuditChain(chain_path)
        recs = []
        recs.append(c.append("a", ticket_id=1))
        recs.append(c.append("b", ticket_id=1))
        recs.append(c.append("c", ticket_id=1))
        for i in range(1, len(recs)):
            assert recs[i]["prev_hash"] == recs[i - 1]["entry_hash"]
        assert recs[0]["prev_hash"] == GENESIS_HASH

    def test_entry_hash_is_deterministic(self, chain_path):
        c = AuditChain(chain_path)
        rec = c.append("det_test", ticket_id=42)
        entry = {k: rec[k] for k in rec if k not in ("entry_hash", "signature")}
        expected = _sha256(_canonical(entry))
        assert rec["entry_hash"] == expected

    def test_reload_continues_chain(self, chain_path):
        c1 = AuditChain(chain_path)
        r1 = c1.append("first", ticket_id=1)
        c2 = AuditChain(chain_path)
        assert c2._seq == 1
        assert c2._prev_hash == r1["entry_hash"]
        r2 = c2.append("second", ticket_id=1)
        assert r2["seq"] == 2
        assert r2["prev_hash"] == r1["entry_hash"]

    def test_verify_valid_unsigned_chain(self, chain_path):
        c = AuditChain(chain_path)
        c.append("a", ticket_id=1)
        c.append("b", ticket_id=1)
        c.append("c", ticket_id=1)
        ok, errors = AuditChain.verify(chain_path)
        assert ok, f"unexpected errors: {errors}"

    def test_verify_detects_tampered_entry(self, chain_path):
        c = AuditChain(chain_path)
        c.append("a", ticket_id=1)
        c.append("b", ticket_id=1)
        with open(chain_path) as f:
            lines = f.readlines()
        rec = json.loads(lines[0])
        rec["detail"] = {"tampered": True}
        lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
        with open(chain_path, "w") as f:
            f.writelines(lines)
        ok, errors = AuditChain.verify(chain_path)
        assert not ok
        assert any("entry_hash" in e for e in errors)

    def test_verify_detects_broken_chain_link(self, chain_path):
        c = AuditChain(chain_path)
        c.append("a", ticket_id=1)
        c.append("b", ticket_id=1)
        c.append("c", ticket_id=1)
        with open(chain_path) as f:
            lines = f.readlines()
        lines.pop(1)
        with open(chain_path, "w") as f:
            f.writelines(lines)
        ok, errors = AuditChain.verify(chain_path)
        assert not ok
        assert any("prev_hash" in e or "seq" in e for e in errors)

    def test_verify_detects_seq_gap(self, chain_path):
        c = AuditChain(chain_path)
        c.append("a", ticket_id=1)
        c.append("b", ticket_id=1)
        with open(chain_path) as f:
            lines = f.readlines()
        rec = json.loads(lines[1])
        rec["seq"] = 5
        entry = {k: rec[k] for k in rec if k not in ("entry_hash", "signature")}
        rec["entry_hash"] = _sha256(_canonical(entry))
        lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
        with open(chain_path, "w") as f:
            f.writelines(lines)
        ok, errors = AuditChain.verify(chain_path)
        assert not ok
        assert any("seq" in e for e in errors)


@pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
class TestAuditChainSigned:
    """Chain integrity with Ed25519 signatures."""

    def test_signed_entries_have_signature(self, chain_path, key_dir):
        c = AuditChain(chain_path, key_dir=key_dir)
        rec = c.append("signed_test", ticket_id=1)
        assert rec["signature"] != ""
        assert len(rec["signature"]) == 128  # 64 bytes hex-encoded

    def test_verify_signed_chain(self, chain_path, key_dir):
        c = AuditChain(chain_path, key_dir=key_dir)
        c.append("a", ticket_id=1)
        c.append("b", ticket_id=1)
        ok, errors = AuditChain.verify(chain_path, key_dir=key_dir)
        assert ok, f"unexpected errors: {errors}"

    def test_verify_detects_forged_signature(self, chain_path, key_dir):
        c = AuditChain(chain_path, key_dir=key_dir)
        c.append("a", ticket_id=1)
        with open(chain_path) as f:
            lines = f.readlines()
        rec = json.loads(lines[0])
        sig = rec["signature"]
        forged = "ff" * 64
        rec["signature"] = forged
        lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
        with open(chain_path, "w") as f:
            f.writelines(lines)
        ok, errors = AuditChain.verify(chain_path, key_dir=key_dir)
        assert not ok
        assert any("signature" in e for e in errors)

    def test_different_keys_fail_verification(self, chain_path, key_dir, tmp_dir):
        c = AuditChain(chain_path, key_dir=key_dir)
        c.append("a", ticket_id=1)
        other_kd = os.path.join(tmp_dir, "other_keys")
        generate_keypair(other_kd)
        ok, errors = AuditChain.verify(chain_path, key_dir=other_kd)
        assert not ok
        assert any("signature" in e for e in errors)
