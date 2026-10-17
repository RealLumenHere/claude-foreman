"""LIMPET federated plugin index: git-repo indexes, local-first discovery.

Implements docs/plugin-registry-spec.md §2-§4 (wq#4094). The index is a git
repo, never a bespoke backend: each configured index is cloned under the
state dir and `search`/`info` are pure local queries over the merged
`index.json`s -- a `git pull` refreshes an index when online, and everything
keeps working against the last sync when not (Limpet is offline-first; an
air-gapped box syncs its index clone over USB like any git repo).

Federation (spec §2): the canonical index is just the first configured
entry; `limpet-tools index add` registers more, Homebrew-tap style. Each
index carries a tier CEILING -- the most trust it may confer. A third-party
index defaults to a `community` ceiling; granting one `vetted` is the
operator explicitly trusting that index's reviewers (spec §5.1). Tier is set
by the index, never by the plugin: a listed tier above the ceiling is
clamped down, so no index can promote a plugin past what the operator
granted the index itself.

Index generation (spec §3, the CI side of the index repo) lives here too:
`generate_index` builds index.json from per-plugin TOML entry files,
enforcing the registry schema, the reverse-DNS namespace rules, and the
licence-required rule at submission time.

Stdlib-only; git runs via subprocess with an injectable runner.
Ref: docs/plugin-registry-spec.md §2-§4, registry_trust.py (§5).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import tomllib

from registry_trust import REGISTRY_NAME_RE, TIERS, safe_name

INDEX_FILE = "index.json"

# Reverse-DNS namespace grammar (spec §2, from the MCP Registry model):
# at least domain.tld.<leaf>, labels lowercase. The leaf must equal the
# plugin name, so a namespace can never smuggle a second identity.
NAMESPACE_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+){2,}$")
CORE_NAMESPACE_PREFIX = "dev.limpet."

_TIER_RANK = {tier: rank for rank, tier in enumerate(TIERS)}
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")  # sha1 or sha256


def clamp_tier(tier: str, ceiling: str) -> str:
    """The less-trusted of (listed tier, index ceiling). Unknown input
    clamps to the floor -- fail-closed, a typo never grants trust."""
    if tier not in _TIER_RANK or ceiling not in _TIER_RANK:
        return "unlisted"
    return tier if _TIER_RANK[tier] >= _TIER_RANK[ceiling] else ceiling


# ---- index configuration + local clones ---------------------------------
def indexes_path(state_dir: str) -> str:
    return os.path.join(state_dir, "indexes.json")


def clone_dir(state_dir: str, index_name: str) -> str:
    return os.path.join(state_dir, "index-clones", index_name)


def load_config(state_dir: str) -> list:
    """The configured indexes, in priority order (first added wins a name
    collision). Each entry: {name, git, ceiling, last_synced}."""
    try:
        with open(indexes_path(state_dir), encoding="utf-8") as fh:
            data = json.load(fh)
        return list(data.get("indexes", [])) if isinstance(data, dict) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_config(state_dir: str, indexes: list) -> None:
    os.makedirs(state_dir, exist_ok=True)
    tmp = indexes_path(state_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"indexes": indexes}, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, indexes_path(state_dir))


def add_index(state_dir: str, name: str, git_url: str, ceiling: str = "community") -> dict:
    """Register an index repo (`limpet-tools index add`). The ceiling
    defaults to community -- an operator grants a third-party index `vetted`
    (or the canonical index `core`) explicitly, never implicitly."""
    if not safe_name(name):
        raise ValueError(
            f"unsafe index name {name!r} (must match {REGISTRY_NAME_RE.pattern}; "
            "it becomes a directory under the state dir)")
    if ceiling not in TIERS[:-1]:
        raise ValueError(f"ceiling {ceiling!r} must be one of {TIERS[:-1]}")
    indexes = load_config(state_dir)
    if any(e["name"] == name for e in indexes):
        raise ValueError(f"an index named {name!r} is already configured")
    entry = {"name": name, "git": git_url, "ceiling": ceiling, "last_synced": None}
    indexes.append(entry)
    save_config(state_dir, indexes)
    return entry


def remove_index(state_dir: str, name: str) -> bool:
    """Unregister an index AND drop its local clone -- a removed index must
    stop answering searches immediately, and a later `index add` under the
    same name must not inherit the old index's records."""
    indexes = load_config(state_dir)
    kept = [e for e in indexes if e["name"] != name]
    if len(kept) == len(indexes):
        return False
    save_config(state_dir, kept)
    if safe_name(name):
        shutil.rmtree(clone_dir(state_dir, name), ignore_errors=True)
    return True


def _run_git(args, runner=None):
    runner = runner or (lambda a: subprocess.run(
        a, capture_output=True, text=True, timeout=120))
    return runner(["git"] + args)


def sync_index(state_dir: str, entry: dict, runner=None):
    """Clone or fast-forward one index's local copy. Network failure is NOT
    an error -- offline-first means search degrades to the last sync -- so
    this returns (synced: bool, detail: str) instead of raising. Only a
    missing clone with no way to create one leaves the index unusable."""
    dest = clone_dir(state_dir, entry["name"])
    if not os.path.isdir(os.path.join(dest, ".git")):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        proc = _run_git(["clone", "--depth", "1", entry["git"], dest], runner)
        if proc.returncode != 0:
            return False, f"clone failed: {proc.stderr.strip()[:200]}"
    else:
        proc = _run_git(["-C", dest, "pull", "--ff-only"], runner)
        if proc.returncode != 0:
            return False, f"offline or pull failed; using last sync ({proc.stderr.strip()[:120]})"
    entry["last_synced"] = time.time()
    indexes = load_config(state_dir)
    for e in indexes:
        if e["name"] == entry["name"]:
            e["last_synced"] = entry["last_synced"]
    save_config(state_dir, indexes)
    return True, "synced"


def load_records(state_dir: str) -> list:
    """Merge every configured index's index.json into one record list.
    Per-record: tier clamped to the index ceiling, `index` set to the source
    index name. First configured index wins a plugin-name collision, so the
    canonical index (added first at provisioning) can never be shadowed by a
    later-added third-party one. Malformed records are skipped, not fatal."""
    records, seen = [], set()
    for entry in load_config(state_dir):
        path = os.path.join(clone_dir(state_dir, entry["name"]), INDEX_FILE)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        for rec in data if isinstance(data, list) else []:
            name = rec.get("name")
            if not safe_name(name) or name in seen:
                continue
            seen.add(name)
            rec = dict(rec)
            rec["tier"] = clamp_tier(rec.get("tier", "community"), entry["ceiling"])
            rec["index"] = entry["name"]
            records.append(rec)
    return records


# ---- discovery (spec §4) -------------------------------------------------
def _relevance(rec: dict, terms: list) -> int:
    """Name hit 3, tag hit 2, description hit 1 -- per term."""
    score = 0
    name = rec.get("name", "")
    tags = [t.lower() for t in rec.get("tags", [])]
    desc = str(rec.get("description", "")).lower()
    for term in terms:
        if term in name:
            score += 3
        if any(term in tag for tag in tags):
            score += 2
        if term in desc:
            score += 1
    return score


def search(records: list, query: str = "", category=None, tag=None,
           tier=None, hardware=None) -> list:
    """Local full-text search over merged records (spec §4): filter, then
    rank tier first (trust-weighted axis, never relevance alone), relevance
    second, community signal (stars+installs) third."""
    terms = [t.lower() for t in query.split() if t.strip()]
    out = []
    for rec in records:
        if category and rec.get("category") != category:
            continue
        if tag and tag.lower() not in [t.lower() for t in rec.get("tags", [])]:
            continue
        if tier and rec.get("tier") != tier:
            continue
        if hardware and hardware not in rec.get("hardware_required", []):
            continue
        score = _relevance(rec, terms)
        if terms and score == 0:
            continue
        out.append((rec, score))
    stats = lambda r: (r.get("stats", {}).get("stars", 0)
                       + r.get("stats", {}).get("installs", 0))
    out.sort(key=lambda pair: (
        _TIER_RANK.get(pair[0].get("tier"), len(TIERS)),
        -pair[1], -stats(pair[0]), pair[0].get("name", "")))
    return [rec for rec, _ in out]


def info(records: list, name: str):
    """The full merged record for `name`, or None."""
    for rec in records:
        if rec.get("name") == name:
            return rec
    return None


# ---- namespaces (spec §2) ------------------------------------------------
def namespace_errors(namespace, name: str, tier: str) -> list:
    """Structural namespace rules, enforced at index submission:
    reverse-DNS shape, leaf == plugin name, and dev.limpet.* reserved for
    project-owned (core/vetted) entries so a community submission cannot
    impersonate a core plugin. OWNERSHIP verification (GitHub OAuth for
    io.github.*, DNS TXT otherwise) is the index repo CI's runtime step --
    inject it via generate_index(ownership_check=...)."""
    errors = []
    if not isinstance(namespace, str) or not NAMESPACE_RE.match(namespace or ""):
        return [f"namespace {namespace!r} must be reverse-DNS "
                f"(e.g. io.github.<user>.<name>) matching {NAMESPACE_RE.pattern}"]
    if namespace.rsplit(".", 1)[-1] != name:
        errors.append(f"namespace {namespace!r} must end in the plugin name {name!r}")
    if namespace.startswith(CORE_NAMESPACE_PREFIX) and tier not in ("core", "vetted"):
        errors.append(
            f"namespace {namespace!r} is project-reserved ({CORE_NAMESPACE_PREFIX}*); "
            f"a {tier!r} entry cannot claim it")
    return errors


# ---- index generation (spec §3, the index repo's CI) ---------------------
REQUIRED_ENTRY_FIELDS = ("name", "version", "category", "description", "license")
REQUIRED_REGISTRY_FIELDS = ("namespace", "git", "rev", "tier")


def validate_entry(data: dict) -> list:
    """Schema-validate one per-plugin TOML entry ([plugin] + [registry] +
    optional [signature]) for listing. Mirrors the §3 field rules: licence
    mandatory, rev a full commit SHA (content address, never a branch),
    names in the strict registry grammar."""
    errors = []
    plugin = data.get("plugin")
    registry = data.get("registry")
    if not isinstance(plugin, dict):
        return ["missing [plugin] table"]
    if not isinstance(registry, dict):
        return ["missing [registry] table"]
    name = plugin.get("name", "")
    if not safe_name(name):
        errors.append(f"[plugin].name {name!r} must match {REGISTRY_NAME_RE.pattern}")
    for field in REQUIRED_ENTRY_FIELDS:
        if not str(plugin.get(field, "") or "").strip():
            errors.append(f"[plugin].{field} is required for a registry listing")
    for field in REQUIRED_REGISTRY_FIELDS:
        if not str(registry.get(field, "") or "").strip():
            errors.append(f"[registry].{field} is required for a registry listing")
    tier = registry.get("tier", "")
    if tier and tier not in TIERS[:-1]:
        errors.append(f"[registry].tier {tier!r} must be one of {TIERS[:-1]}")
    rev = str(registry.get("rev", ""))
    if rev and not _FULL_SHA_RE.match(rev):
        errors.append(
            f"[registry].rev {rev!r} must be a full commit SHA "
            "(a pinned content address, never a branch or tag)")
    if registry.get("namespace") and safe_name(name):
        errors.extend(namespace_errors(registry["namespace"], name, tier))
    te = registry.get("tool_energize")
    if te is not None:
        # Per-tool energize override (spec §5.4): a [registry.tool_energize]
        # table of tool-name = true/false that overrides the plugin-wide
        # needs_energize for individual tools, so a read/scan tool (jtag_scan)
        # is not gated as heavily as a write tool (flash_write) on the same
        # plugin. Optional and backward compatible — absent means every tool
        # inherits needs_energize.
        if not isinstance(te, dict):
            errors.append(
                "[registry.tool_energize] must be a table of "
                "tool-name = true/false per-tool energize overrides")
        else:
            for tool, flag in te.items():
                if not isinstance(flag, bool):
                    errors.append(
                        f"[registry.tool_energize].{tool} must be a boolean "
                        f"(got {flag!r}) — it overrides the plugin-wide "
                        "needs_energize for that one tool")
    sig = data.get("signature")
    if sig is not None:
        for field in ("scheme", "pubkey_id", "sig", "tree_hash"):
            if not str(sig.get(field, "") or "").strip():
                errors.append(f"[signature].{field} is required when [signature] is present")
    return errors


def entry_to_record(data: dict) -> dict:
    """One validated TOML entry -> one index.json record (spec §3)."""
    plugin, registry = data["plugin"], data["registry"]
    rec = {
        "name": plugin["name"],
        "namespace": registry["namespace"],
        "version": plugin["version"],
        "category": plugin["category"],
        "description": plugin["description"],
        "license": plugin["license"],
        "git": registry["git"],
        "rev": registry["rev"],
        "tier": registry["tier"],
        "tags": list(registry.get("tags", [])),
        "needs_energize": bool(registry.get("needs_energize", False)),
        "tool_energize": {
            str(tool): bool(flag)
            for tool, flag in (registry.get("tool_energize") or {}).items()},
        "hardware_required": list(registry.get("hardware_required", [])),
        "limpet_compat": registry.get("limpet_compat", ""),
    }
    sig = data.get("signature")
    if sig:
        rec["signature"] = {
            "scheme": sig["scheme"], "pubkey_id": sig["pubkey_id"],
            "pubkey": sig.get("pubkey", ""), "sig": sig["sig"],
            "tree_hash": sig["tree_hash"]}
    return rec


def generate_index(entries_dir: str, ownership_check=None):
    """Build the index.json record list from a directory of per-plugin TOML
    entry files (the index repo's CI step, spec §3/§5.4.1). Returns
    (records, errors_by_file). `ownership_check(namespace, entry_dict) ->
    error-or-None` is the injectable submission-time ownership verifier
    (GitHub OAuth / DNS TXT); structural namespace rules always run."""
    records, errors, seen = [], {}, set()
    for fname in sorted(os.listdir(entries_dir)):
        if not fname.endswith(".toml"):
            continue
        path = os.path.join(entries_dir, fname)
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            errors[fname] = [f"unreadable entry: {exc}"]
            continue
        problems = validate_entry(data)
        if not problems:
            name = data["plugin"]["name"]
            if name in seen:
                problems = [f"duplicate plugin name {name!r}"]
            elif ownership_check is not None:
                failure = ownership_check(data["registry"]["namespace"], data)
                if failure:
                    problems = [f"namespace ownership: {failure}"]
        if problems:
            errors[fname] = problems
            continue
        seen.add(data["plugin"]["name"])
        records.append(entry_to_record(data))
    return records, errors


def write_index(entries_dir: str, out_path: str, ownership_check=None):
    """Generate and atomically write index.json. Any entry error aborts the
    write (the CI gate: a bad submission never reaches the published index)."""
    records, errors = generate_index(entries_dir, ownership_check=ownership_check)
    if errors:
        return records, errors
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, out_path)
    return records, errors
