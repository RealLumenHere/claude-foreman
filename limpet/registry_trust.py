"""LIMPET plugin-registry trust: content-bound signatures, re-verified on load.

Implements docs/plugin-registry-spec.md §5 (wq#4094). The structural fix for
the VSCode spoofable-badge failure: trust is a signature over the tuple
(name, git, rev) PLUS a tree hash of the pinned tree's per-file hash list,
so it is bound to exact bytes, never to a mutable server flag. Verification
happens on every registry load (plugin_registry.py scan gate), not just at
install -- a tampered-on-disk plugin fails to load.

Scheme: minisign baseline (offline-verifiable, no infra); the verifier is a
plain callable so sigstore/cosign can slot in (spec §5.2.2) and tests can
inject a fake. Fail-closed throughout: no minisign binary, no receipt tree,
a hash mismatch, or an unknown tier each reject rather than warn.

Trust roots live in /etc/limpet/trust/ (LIMPET_TRUST_DIR): `core.pub` is the
project root key, `vetted-*.pub` are delegated reviewer keys, `index-*.pub`
are operator-added keys for trusted third-party indexes. A community-tier
plugin is verified against the author key recorded in the index at
namespace-verified submission time (carried in its install receipt) -- the
index listing is its discovery anchor, the signature its content anchor.

Stdlib-only (house rule). Pure helpers up top; the receipt store and the
load gate own the filesystem. Ref: docs/plugin-registry-spec.md §5, §7.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

DEFAULT_TRUST_DIR = "/etc/limpet/trust"

# Registry name grammar (spec §5.2.5) -- deliberately STRICTER than
# manifest.py's _NAME_RE (which allows dots/underscores for on-box drop-ins):
# anything written under plugins.d/ or the plugin store on behalf of a remote
# index entry must pass this before any filesystem write happens.
REGISTRY_NAME_RE = re.compile(r"^[a-z0-9-]{2,32}$")

# Trust tiers, most to least trusted (spec §5.1). "unlisted" is the below-
# community floor for a direct git install that appears in no index.
TIERS = ("core", "vetted", "community", "unlisted")

SCHEMES = ("minisign", "sigstore")


class TrustError(Exception):
    """A trust-model failure (missing verifier binary, malformed receipt).
    Distinct from a plain verification *mismatch*, which is reported as
    error strings so the caller can surface every problem at once."""


def safe_name(name) -> bool:
    """True if `name` is safe to use as a filename under plugins.d/ or the
    plugin store: registry grammar only, so no path separators, no dots (no
    traversal), no shell metacharacters. Checked BEFORE any write."""
    return isinstance(name, str) and bool(REGISTRY_NAME_RE.match(name))


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: str) -> str:
    """sha256 over the sorted per-file hash list of a plugin tree (spec §5.2.1).

    Every regular file under `root` except the `.git` dir contributes one
    line `<relative-posix-path>\\0<sha256>\\n`; the lines are sorted and the
    digest of the concatenation is the tree hash. Symlinks hash their target
    path (not the pointed-to content) so a link redirected after signing
    changes the hash. Deterministic across checkouts of the same rev."""
    lines = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for fname in sorted(filenames):
            path = os.path.join(dirpath, fname)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if os.path.islink(path):
                digest = hashlib.sha256(os.readlink(path).encode()).hexdigest()
            else:
                digest = file_sha256(path)
            lines.append(f"{rel}\0{digest}\n")
    h = hashlib.sha256()
    for line in sorted(lines):
        h.update(line.encode())
    return "sha256:" + h.hexdigest()


def signing_payload(name: str, git: str, rev: str, tree_hash_value: str) -> bytes:
    """The exact bytes a plugin signature covers: canonical JSON over the
    content-bound tuple (spec §5.2.1). Sorted keys + fixed separators so the
    same tuple always yields byte-identical payloads for signer and verifier."""
    return json.dumps(
        {"name": name, "git": git, "rev": rev, "tree_hash": tree_hash_value},
        sort_keys=True, separators=(",", ":")).encode()


def minisign_verify(payload: bytes, sig: str, pubkey: str) -> bool:
    """Verify a detached minisign signature over `payload`. Writes the three
    pieces into a private tempdir and shells out to minisign -V. Raises
    TrustError (fail closed) when the minisign binary is absent -- an
    unverifiable signature must never pass silently."""
    tmp = tempfile.mkdtemp(prefix="limpet-trust-")
    try:
        paths = {}
        for fname, content in (("payload", payload),
                               ("payload.minisig", sig.encode()),
                               ("key.pub", pubkey.encode())):
            paths[fname] = os.path.join(tmp, fname)
            with open(paths[fname], "wb") as fh:
                fh.write(content)
        try:
            proc = subprocess.run(
                ["minisign", "-V", "-q",
                 "-m", paths["payload"],
                 "-x", paths["payload.minisig"],
                 "-p", paths["key.pub"]],
                capture_output=True, timeout=30)
        except FileNotFoundError as exc:
            raise TrustError(
                "minisign binary not found; cannot verify signatures "
                "(fail-closed, spec §5.2.2)") from exc
        return proc.returncode == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load_trust_roots(trust_dir=None) -> dict:
    """name-stem -> public key text for every *.pub under the trust dir.
    Missing dir = no roots (core/vetted verification then fails closed)."""
    trust_dir = trust_dir or os.environ.get("LIMPET_TRUST_DIR") or DEFAULT_TRUST_DIR
    roots = {}
    if not os.path.isdir(trust_dir):
        return roots
    for entry in sorted(os.listdir(trust_dir)):
        if entry.endswith(".pub"):
            with open(os.path.join(trust_dir, entry), encoding="utf-8") as fh:
                roots[entry[: -len(".pub")]] = fh.read()
    return roots


def roots_for_tier(tier: str, roots: dict, receipt_pubkey: str = "") -> list:
    """Which public keys may vouch for a plugin of `tier` (spec §5.1 table).

    core      -> the project root key only.
    vetted    -> project root or any delegated reviewer key (vetted-*).
    community -> the author key recorded at namespace-verified index
                 submission (carried in the receipt), plus any operator-added
                 index keys (index-*) the operator chose to trust.
    unlisted  -> the self-published key from the repo, if any (floor tier;
                 the gate still sandboxes it via tier policy)."""
    if tier == "core":
        return [roots[k] for k in roots if k == "core"]
    if tier == "vetted":
        return [roots[k] for k in sorted(roots) if k == "core" or k.startswith("vetted-")]
    keys = [roots[k] for k in sorted(roots) if k.startswith("index-")]
    if receipt_pubkey:
        keys.append(receipt_pubkey)
    return keys


# ---- install receipts --------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Receipt:
    """What `limpet-tools install` records so every later load can re-verify
    the exact content it installed. `tree` is the installed plugin checkout
    the tree hash is recomputed over; `elevated` is the operator's explicit
    per-plugin privilege grant for community/unlisted tiers (spec §7.2.1)."""
    name: str
    git: str
    rev: str
    tree_hash: str
    tier: str
    tree: str
    scheme: str = "minisign"
    pubkey_id: str = ""
    pubkey: str = ""
    sig: str = ""
    elevated: bool = False
    installed_at: float = 0.0


def receipts_dir(state_dir: str) -> str:
    return os.path.join(state_dir, "trust")


def receipt_path(state_dir: str, name: str) -> str:
    if not safe_name(name):
        raise TrustError(f"unsafe plugin name {name!r} (must match {REGISTRY_NAME_RE.pattern})")
    return os.path.join(receipts_dir(state_dir), f"{name}.json")


def save_receipt(state_dir: str, receipt: Receipt) -> str:
    path = receipt_path(state_dir, receipt.name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(dataclasses.asdict(receipt), fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def load_receipt(state_dir: str, name: str):
    """Receipt for `name`, or None if the plugin was never installed through
    the registry (a hand-dropped manifest). A malformed receipt raises --
    a plugin that CLAIMS registry provenance must prove it."""
    path = receipt_path(state_dir, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    try:
        return Receipt(**data)
    except TypeError as exc:
        raise TrustError(f"malformed trust receipt {path}: {exc}") from exc


# ---- verification ------------------------------------------------------
def verify_receipt(receipt: Receipt, verifier=None, trust_dir=None) -> list:
    """Re-verify an installed plugin NOW (spec §5.2.4): recompute the tree
    hash over the installed checkout, rebuild the signing payload, and check
    the signature against the keys the plugin's tier is allowed to use.
    Returns a list of human-readable problems; empty list = trusted."""
    verifier = verifier or minisign_verify
    errors = []
    if receipt.tier not in TIERS:
        return [f"unknown trust tier {receipt.tier!r}"]
    if receipt.scheme not in SCHEMES:
        return [f"unknown signature scheme {receipt.scheme!r}"]
    if not os.path.isdir(receipt.tree):
        return [f"installed tree {receipt.tree} is missing"]
    actual = tree_hash(receipt.tree)
    if actual != receipt.tree_hash:
        errors.append(
            f"tree hash mismatch: installed tree is {actual}, receipt pinned "
            f"{receipt.tree_hash} (on-disk content was modified after install)")
    if receipt.tier == "unlisted" and not receipt.sig:
        # The floor tier may be unsigned (direct git install, spec §5.1);
        # the tree-hash pin above still catches post-install tampering and
        # tier policy still sandboxes it.
        return errors
    if not receipt.sig:
        return errors + [f"{receipt.tier}-tier plugin has no signature"]
    payload = signing_payload(receipt.name, receipt.git, receipt.rev, receipt.tree_hash)
    keys = roots_for_tier(receipt.tier, load_trust_roots(trust_dir), receipt.pubkey)
    if not keys:
        return errors + [
            f"no trust root available for tier {receipt.tier!r} "
            f"(populate {trust_dir or DEFAULT_TRUST_DIR})"]
    if not any(verifier(payload, receipt.sig, key) for key in keys):
        errors.append(
            f"signature over (name, git, rev) + tree_hash does not verify "
            f"against any tier-{receipt.tier} trust root")
    return errors


def tier_policy_errors(manifest, receipt) -> list:
    """The wq#3961 privilege reconciliation (spec §7.2.1): only core and
    vetted plugins may request escalation. A community/unlisted plugin whose
    [mutation] declares escalates=true cannot load until the operator
    explicitly elevates it per-plugin (`limpet-tools elevate`). Energize
    confirmation is NOT handled here -- that is the broker's runtime gate
    (plugin-spec.md §6.2) and is never waivable by tier."""
    escalates = bool(manifest.mutation and manifest.mutation.escalates)
    if not escalates:
        return []
    tier = receipt.tier if receipt else "unlisted"
    elevated = bool(receipt and receipt.elevated)
    if tier in ("core", "vetted") or elevated:
        return []
    return [
        f"tier policy: {tier}-tier plugin declares [mutation].escalates=true; "
        "privilege is core/vetted-only -- review it up a tier or elevate it "
        "explicitly with `limpet-tools elevate` (spec §7.2)"]


def trust_gate(state_dir: str, trust_dir=None, verifier=None):
    """The re-verify-on-load hook plugin_registry.Registry calls for every
    scanned drop-in. Returns a callable (name, manifest) -> list of errors;
    any error rejects the plugin from loading at all (fail-closed).

    A drop-in with no receipt is a hand-installed manifest: it loads (the
    pre-registry workflow keeps working) but gets no tier above the unlisted
    floor, so tier policy still refuses it any privilege."""
    def gate(name, manifest):
        # A name outside the registry grammar can never have a receipt
        # (receipt_path refuses to touch it): treat it as a plain hand
        # drop-in -- manifest.py's looser grammar governs those.
        if not safe_name(name):
            return tier_policy_errors(manifest, None)
        try:
            receipt = load_receipt(state_dir, name)
        except TrustError as exc:
            return [str(exc)]
        errors = []
        if receipt is not None:
            try:
                errors.extend(verify_receipt(receipt, verifier=verifier, trust_dir=trust_dir))
            except TrustError as exc:
                errors.append(str(exc))
        errors.extend(tier_policy_errors(manifest, receipt))
        return errors
    return gate
